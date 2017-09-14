#!/bin/bash

# Fail on any error.
set -e
# Display commands to stderr.
set -x

# Set up Python 3.6.1 environment.
eval "$(pyenv init -)"
eval 'pyenv shell 3.6.1'
export PIP_COMMAND='python3.6 -m pip'
# Ensure that python 3 is used.
# Filter out tests that support only python 2.
export BAZEL_TEST_ARGS='--force_python=py3  --test_tag_filters=-py2only \
  --build_tag_filters=-py2only'

cd github/magenta
kokoro/test.sh
