# Third-party notices
本项目按根目录 MIT 许可证提供；第三方依赖保持各自许可证，不能一概改成 MIT。下表基于已验证的干净安装的包元数据和随包许可证文件，涵盖运行依赖与开发/测试工具。版本来自 `requirements-lock.txt` 和 `frontend/package-lock.json`。
当前交付是源码与合成数据，不打包 Python、Node、node_modules、venv 或第三方二进制；安装时从官方注册库获取原始依赖及许可文件。若另行分发二进制或依赖包，须随发行物附带相应原始许可证和通知。
## Python dependency inventory
| Package | Version | License |
| --- | --- | --- |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.141.1 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.51.0 | BSD-3-Clause |

certifi 使用 MPL-2.0，本项目未修改或复制其源文件/证书库。MPL 是文件级许可证，不能把它称为 MIT；源码及许可证随上游包提供。Python 标准库与解释器采用其自身 PSF 许可，不随本仓库重新分发。
## npm lockfile inventory
下表包括 lockfile 中其他平台的可选原生包，表示锁定依赖的元数据检查，不表示已安装或验证全部平台。Windows x64 干净安装实际为 26 个包；未安装的其他平台包未核对其归档内许可文本。
| Package | Version | License | Role |
| --- | --- | --- | --- |
| @oxc-project/types | 0.155.0 | MIT | development/optional |
| @rolldown/binding-android-arm-eabi | 1.2.15 | MIT | development/optional |
| @rolldown/binding-android-arm64 | 1.2.15 | MIT | development/optional |
| @rolldown/binding-darwin-arm64 | 1.2.15 | MIT | development/optional |
| @rolldown/binding-darwin-x64 | 1.2.15 | MIT | development/optional |
| @rolldown/binding-freebsd-x64 | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-arm-gnueabihf | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-arm64-gnu | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-arm64-musl | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-ppc64-gnu | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-s390x-gnu | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-x64-gnu | 1.2.15 | MIT | development/optional |
| @rolldown/binding-linux-x64-musl | 1.2.15 | MIT | development/optional |
| @rolldown/binding-openharmony-arm64 | 1.2.15 | MIT | development/optional |
| @rolldown/binding-win32-arm64-msvc | 1.2.15 | MIT | development/optional |
| @rolldown/binding-win32-x64-msvc | 1.2.15 | MIT | development/optional |
| @rolldown/pluginutils | 1.0.1 | MIT | development/optional |
| @types/node | 26.6.5 | MIT | development/optional |
| @types/react | 19.3.0 | MIT | development/optional |
| @types/react-dom | 19.3.0 | MIT | development/optional |
| @typescript/typescript-aix-ppc64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-darwin-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-freebsd-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-freebsd-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-arm | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-loong64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-mips64el | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-ppc64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-riscv64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-s390x | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-netbsd-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-netbsd-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-openbsd-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-openbsd-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-sunos-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-win32-arm64 | 7.0.2 | Apache-2.0 | development/optional |
| @typescript/typescript-win32-x64 | 7.0.2 | Apache-2.0 | development/optional |
| @vitejs/plugin-react | 6.1.2 | MIT | development/optional |
| csstype | 3.2.3 | MIT | development/optional |
| detect-libc | 2.1.2 | Apache-2.0 | development/optional |
| fdir | 6.5.0 | MIT | development/optional |
| fsevents | 2.3.3 | MIT | development/optional |
| lightningcss | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 | development/optional |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 | development/optional |
| nanoid | 3.3.20 | MIT | development/optional |
| picocolors | 1.1.1 | ISC | development/optional |
| picomatch | 4.0.7 | MIT | development/optional |
| postcss | 8.5.32 | MIT | development/optional |
| react | 19.3.1 | MIT | runtime |
| react-dom | 19.3.1 | MIT | runtime |
| rolldown | 1.2.15 | MIT | development/optional |
| scheduler | 0.28.0 | MIT | runtime |
| source-map-js | 1.2.2 | BSD-3-Clause | development/optional |
| tinyglobby | 0.2.17 | MIT | development/optional |
| typescript | 7.0.2 | Apache-2.0 | development/optional |
| undici-types | 8.9.0 | MIT | development/optional |
| vite | 8.3.5 | MIT | development/optional |

lightningcss 及其原生构建采用 MPL-2.0；本项目仅使用未修改的构建工具，不将其二进制纳入源码交付。TypeScript 及原生编译器包采用 Apache-2.0，需保留其原始 LICENSE/NOTICE；这些文件仍由依赖安装提供，项目没有重新分发编译器。@rolldown/binding-win32-x64-msvc 的独立包元数据声明 MIT，但归档没有单独 LICENSE 文本；本次源码交付不携带该原生包，若以后分发它须一并核对上游 rolldown 原始许可证。
## Frontend runtime licenses
构建产物包含 React、React DOM、scheduler 的代码。以下逐字保留其随包许可证；在分发前端构建物时应一并携带本文件。开发工具的许可证和嵌入式通知以安装包中的原始文件为准。

### react 19.3.1

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

### react-dom 19.3.1

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

### scheduler 0.28.0

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

## Model service
DeepSeek 是可选的托管 API，并非本项目分发的开源模型权重。API 使用、费用与服务条款由服务商规定；本项目 MIT 许可证不授权模型权重或第三方托管服务。模型默认关闭，密钥只存在于运行环境。
