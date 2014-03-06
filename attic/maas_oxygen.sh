#!/bin/bash

error() { echo "$@" 1>&2; }
fail() { [ $# -eq 0 ] || error "$@"; exit 1; }

out_d="${OUT_D:-/srv/maas-images/ephemeral}"
script=$(readlink -f "$0")
my_dir=$(dirname "$script")
SIGN_HOME=${SIGN_HOME:-/srv/jenkins} # set SIGN_HOME="$HOME" to override this
SSBZR=${SSBZR:-lp:simplestreams}
# Check out generate the data
[ -d "${WORKSPACE}/sstreams" ] ||
    bzr branch "$SSBZR" "$WORKSPACE/sstreams" ||
    fail "failed to branch to $WORKSPACE/sstreams"

index_sign() {
    python ${my_dir}/oxygen_indices --base_d ${1} ||
            fail "Failed to create indicies for ${1}"

    ( cd sstreams &&
        env ${SIGN_HOME:+"HOME=${SIGN_HOME}"} \
            PYTHONPATH=$PWD ./tools/js2signed ${1}
    ) || fail "failed to sign files using js2signed for ${1}"

    scripts/bzr_commit.sh ${1} ${BUILD_TAG} ||
        fail "failed to bzr commit to ${1}"
}

python ${my_dir}/tree2o2.py \
        --base_d /srv/maas-images/ephemeral \
        --namespace com.ubuntu.maas \
        --subid ephemeral \
        --stream releases \
        --out_d ${out_d} ||
            fail "Failed to process tree files"

echo "--------------------------------------------"
echo "--------------------------------------------"
echo "--------------------------------------------"

python ${my_dir}/tree2o2.py \
        --base_d /srv/maas-images/ephemeral \
        --namespace com.ubuntu.maas \
        --subid ephemeral \
        --stream daily \
        --out_d ${out_d} ||
            fail "Failed to process tree files"

echo "--------------------------------------------"
echo "--------------------------------------------"
echo "--------------------------------------------"

index_sign /srv/maas-images/ephemeral/daily/streams/v1
index_sign /srv/maas-images/ephemeral/releases/streams/v1
