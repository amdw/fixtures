#!/usr/bin/env python3
# Copyright 2025 Andrew Medworth
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

"""Simple test runner that automatically discovers tests with the correct pattern."""

import sys
import unittest


def main():
    """Run all tests using discovery with our custom pattern."""
    # Configure test discovery
    loader = unittest.TestLoader()

    # Discover tests with our pattern
    suite = loader.discover(start_dir=".", pattern="*test*.py", top_level_dir=".")

    # Run tests with appropriate verbosity
    verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1

    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        failfast="--failfast" in sys.argv,
        buffer="--buffer" in sys.argv,
    )

    result = runner.run(suite)

    # Exit with error code if tests failed
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
