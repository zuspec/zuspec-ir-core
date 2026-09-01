
#****************************************************************************
# Copyright 2019-2025 Matthew Ballance and contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#****************************************************************************

# BASE is the released version and is what a `v<BASE>` tag must match; CI
# rejects a tag that disagrees with it. SUFFIX is stamped by CI for
# non-release builds so a development wheel can never collide with a release.
#
# 0.1.0 rather than 0.0.1: PyPI already holds run-id-stamped dev releases of
# sibling packages numbered 0.0.1.<run-id>, which sort ABOVE a bare 0.0.1.
# The whole estate moves to 0.1.0 so that a real release outranks that stream.
BASE="0.1.0"
SUFFIX=""
VERSION="%s%s" % (BASE, SUFFIX)
version=VERSION
