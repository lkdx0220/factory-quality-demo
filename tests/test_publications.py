"""Final artifacts, actual HTTP channels, source closure and publication faults."""

import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_fact_registry as helpers
from fastapi.testclient import TestClient
from mdlab_app.api import create_app
from mdlab_app.ingestion import canonical
from mdlab_app.plan_contracts import hash_object
from mdlab_app.publication_contracts import Publication
from mdlab_app.publication_templates import render

PREFIX = helpers.PREFIX


class PublicationTests(unittest.TestCase):
    setUp = helpers.FactRegistryTests.setUp
    tearDown = helpers.FactRegistryTests.tearDown
    data = helpers.FactRegistryTests.data
    execution = helpers.FactRegistryTests.execution
    facts = helpers.FactRegistryTests.facts
    path = helpers.FactRegistryTests.path
    proposal = helpers.FactRegistryTests.proposal
    validate = helpers.FactRegistryTests.validate
    rewrite_execution = helpers.FactRegistryTests.rewrite_execution

    def ready(self, at="2026-09-07T00:00:00Z"):
        facts = self.facts(self.execution(at))
        receipt = self.validate(facts, self.proposal(facts))
        return facts, {"fact_set_digest": facts["fact_set_digest"], "validation_id": receipt["validation_id"]}

    def publish(self, facts, body):
        return self.client.post(self.path(facts, "/publish"), json=body)

    def pubpath(self, view, suffix=""):
        return PREFIX + "/publications/" + view["publication_id"] + suffix

    def outputs(self, view):
        return [self.client.get(self.pubpath(view)), self.client.get(view["details_url"]), self.client.get(view["export_url"])]

    def assert_blocked(self, responses, status=409):
        for response in responses:
            self.assertEqual(response.status_code, status, response.text)
            self.assertFalse(response.json()["ok"])
            self.assertNotIn('"answer_published":true', response.text)
            self.assertNotIn('"operations":', response.text)

    def test_normal_final_answer_is_complete_idempotent_and_does_not_write_business(self):
        before = self.db.read_bytes()
        facts, body = self.ready()
        view = self.data(self.publish(facts, body))
        self.assertEqual(view["status"], "verified_answer")
        self.assertTrue(view["answer_published"])
        self.assertFalse(view["model_output_used"])
        self.assertEqual((view["total_rows"], view["preview_rows"]), (4, 3))
        self.assertEqual(len(view["summary"]), 7)
        self.assertEqual(view["completeness"]["rendered_facts"], 35)
        self.assertIn("不表示产品合格", view["status_label"])
        repeated = self.data(self.publish(facts, body))
        self.assertTrue(repeated["reused"])
        self.assertEqual(repeated["publication_id"], view["publication_id"])
        response = self.client.get(view["export_url"])
        self.assertIn('attachment; filename="verified-lot-inventory.json"', response.headers['content-disposition'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        artifact = self.data(response)
        doc = artifact["publication"]
        Publication.model_validate(doc)
        self.assertEqual(doc["rendered_fact_ids"], facts["required_fact_ids"])
        self.assertEqual(doc["validation_digest"], hash_object(artifact["validation_receipt"]))
        self.assertEqual(doc["publication_digest"], hash_object({k: v for k, v in doc.items() if k not in ("publication_id", "publication_digest")}))
        self.assertEqual(len(doc["operations"]), 4)
        self.assertEqual(before, self.db.read_bytes())

    def test_zero_and_inclusive_historical_boundary_can_publish(self):
        for at, expected in (("2026-09-01T02:00:00Z", 0), ("2026-09-01T05:09:59Z", 0), ("2026-09-01T05:10:00Z", 1)):
            facts, body = self.ready(at)
            view = self.data(self.publish(facts, body))
            self.assertEqual(view["total_rows"], expected)
            self.assertEqual(view["summary"][1]["value"], at)
            self.assertEqual(view["completeness"]["rendered_facts"], 7 + 7 * expected)
            self.assertEqual(self.data(self.client.get(view["details_url"]))["total"], expected)

    def test_full_detail_pages_have_all_rows_and_reject_wrong_version_and_extra_queries(self):
        facts, body = self.ready()
        view = self.data(self.publish(facts, body))
        rows = []
        for offset in (0, 2):
            page = self.data(self.client.get(view["details_url"] + f"&offset={offset}&limit=2"))
            self.assertEqual(page["total"], 4)
            rows += page["items"]
        self.assertEqual([r["operation_id"] for r in rows], ["OP-0001", "OP-0002", "OP-0007", "OP-0012"])
        wrong = view["details_url"].replace("publication_digest=" + view["publication_digest"], "publication_digest=" + "0" * 64)
        self.assert_blocked([self.client.get(wrong)])
        for url in (view["details_url"] + "&bad=1", view["export_url"] + "?bad=1", view["details_url"] + "&offset=1&offset=2"):
            self.assertEqual(self.client.get(url).status_code, 422)

    def test_no_receipt_rejected_receipt_cross_set_and_wrong_version_cannot_publish(self):
        facts, body = self.ready()
        missing = {**body, "validation_id": "validation-" + "0" * 32}
        self.assert_blocked([self.publish(facts, missing)])
        proposal = self.proposal(facts); proposal['claims'].pop()
        bad = self.validate(facts, proposal)
        self.assert_blocked([self.publish(facts, {**body, 'validation_id': bad['validation_id']})])
        other, other_body = self.ready()
        self.assert_blocked([self.publish(other, body), self.publish(facts, other_body),
                             self.publish(facts, {**body, 'fact_set_digest': '0' * 64})])

    def test_free_text_model_status_and_raw_evidence_inputs_are_not_accepted_or_echoed(self):
        facts, body = self.ready()
        for key in ('summary', 'title', 'warnings', 'status', 'evidence', 'scope', 'answer_published', 'template_version'):
            response = self.publish(facts, {**body, key: 'UNTRUSTED_OUTPUT_SENTINEL'})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('UNTRUSTED_OUTPUT_SENTINEL', response.text)
        self.assertEqual(self.publish(facts, {**body, 'validation_id': True}).status_code, 422)

    def test_cross_session_all_channels_and_publish_are_denied(self):
        facts, body = self.ready()
        view = self.data(self.publish(facts, body))
        with TestClient(self.app, raise_server_exceptions=False) as stranger:
            for url in (self.pubpath(view), view['details_url'], view['export_url']):
                self.assertEqual(stranger.get(url).status_code, 404)
            self.assertEqual(stranger.post(self.path(facts, '/publish'), json=body).status_code, 404)

    def test_expiry_blocks_repeated_publish_read_details_and_export(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        original = self.plans.clock
        self.plans.clock = lambda: original() + 1801
        self.assert_blocked([self.publish(facts, body), *self.outputs(view)])

    def test_permission_revocation_blocks_all_channels(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        self.plans.revoke('session', self.client.cookies.get('demo_plan_session'))
        self.assert_blocked([self.publish(facts, body), *self.outputs(view)], 403)

    def test_coverage_revocation_blocks_all_channels(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        plan = self.plans.confirmed(self.client.cookies.get('demo_plan_session'), facts['confirmed_plan_id'])
        self.plans.revoke('coverage', plan['card']['coverage_receipt']['declaration_digest'])
        self.assert_blocked([self.publish(facts, body), *self.outputs(view)])

    def test_exact_bundle_revocation_blocks_all_channels(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        self.plans.revoke('bundle', facts['contract_bundle_hash'])
        self.assert_blocked([self.publish(facts, body), *self.outputs(view)])

    def test_evidence_missing_or_execution_failed_blocks_existing_publication(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        self.rewrite_execution(facts['execution_id'], lambda doc: doc['evidence'].pop())
        self.assert_blocked([self.publish(facts, body), *self.outputs(view)])

    def test_rehashed_publication_text_row_omission_and_identity_tampering_rejected(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        with self.plans.transaction() as con:
            original = json.loads(con.execute('SELECT document FROM publications').fetchone()[0])
        for mutate in (lambda d: d.update(title='UNTRUSTED_OUTPUT_SENTINEL'), lambda d: d['operations'].pop(),
                       lambda d: d.update(validation_id='validation-' + '0' * 32)):
            doc = copy.deepcopy(original); mutate(doc)
            with self.plans.transaction() as con:
                con.execute('UPDATE publications SET document=?,digest=?', (canonical(doc), hash_object(doc)))
            self.assert_blocked(self.outputs(view))
            self.assertNotIn('UNTRUSTED_OUTPUT_SENTINEL', ''.join(r.text for r in self.outputs(view)))

    def test_semantically_rehashed_new_identity_still_cannot_change_fixed_output(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        with self.plans.transaction() as con:
            doc = json.loads(con.execute('SELECT document FROM publications').fetchone()[0])
            doc['title'] = 'UNTRUSTED_OUTPUT_SENTINEL'
            doc['publication_digest'] = hash_object({k:v for k,v in doc.items() if k not in ('publication_id','publication_digest')})
            doc['publication_id'] = 'pub-' + doc['publication_digest']
            con.execute('UPDATE publications SET id=?,document=?,digest=?', (doc['publication_id'], canonical(doc), hash_object(doc)))
        response = self.client.get(PREFIX + '/publications/' + doc['publication_id'] + '/export')
        self.assert_blocked([response])
        self.assertNotIn('UNTRUSTED_OUTPUT_SENTINEL', response.text)

    def test_actual_insert_then_storage_failure_rolls_back_without_success(self):
        facts, body = self.ready()
        service = self.app.state.publication_service; real = service._save; attempts = []
        def fail(con, owner, doc):
            real(con, owner, doc); attempts.append(doc['publication_id'])
            raise sqlite3.OperationalError('PRIVATE_STORAGE_SENTINEL')
        with patch.object(service, '_save', fail):
            response = self.publish(facts, body)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE_STORAGE_SENTINEL', response.text)
        self.assertNotIn('"answer_published":true', response.text)
        self.assertEqual(len(attempts), 1)
        with self.plans.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM publications').fetchone()[0], 0)
        self.assertTrue(self.data(self.publish(facts, body))['answer_published'])

    def test_expiry_during_render_is_rechecked_before_insert(self):
        facts, body = self.ready(); original = self.plans.clock
        def expire(*args):
            result = render(*args)
            self.plans.clock = lambda: original() + 1801
            return result
        with patch('mdlab_app.publication_service.render', expire):
            self.assert_blocked([self.publish(facts, body)])
        with self.plans.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM publications').fetchone()[0], 0)

    def test_rejected_later_candidate_does_not_rewrite_previously_bound_accepted_receipt(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        wrong = self.proposal(facts); wrong['claims'][0]['value'] = 'UNTRUSTED_OUTPUT_SENTINEL'
        bad = self.validate(facts, wrong)
        self.assertFalse(bad['accepted'])
        self.assert_blocked([self.publish(facts, {**body, 'validation_id': bad['validation_id']})])
        artifact = self.data(self.client.get(view['export_url']))
        self.assertEqual(artifact['validation_receipt']['validation_id'], body['validation_id'])
        self.assertNotIn('UNTRUSTED_OUTPUT_SENTINEL', canonical(artifact))

    def test_expiry_during_output_reconstruction_blocks_each_read_channel(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        original = self.plans.clock
        def expire(*args):
            result = render(*args)
            self.plans.clock = lambda: original() + 1801
            return result
        for url in (self.pubpath(view), view['details_url'], view['export_url']):
            self.plans.clock = original
            with patch('mdlab_app.publication_service.render', expire):
                self.assert_blocked([self.client.get(url)])

    def test_restart_preserves_final_artifact_identity(self):
        facts, body = self.ready(); view = self.data(self.publish(facts, body))
        with TestClient(create_app(self.db, helpers.BATCH, helpers.SCHEMA), raise_server_exceptions=False) as client:
            client.cookies.update(self.client.cookies)
            restarted = self.data(client.get(view['export_url']))
            self.assertEqual(restarted['publication']['publication_id'], view['publication_id'])
            self.assertEqual(restarted['publication']['publication_digest'], view['publication_digest'])


if __name__ == '__main__':
    unittest.main()
