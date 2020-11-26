#!/bin/bash

set -e

DOCKER_DEV_IMAGE=${DOCKER_DEV_REGISTRY}/gordo
DOCKER_PROD_IMAGE=${DOCKER_PROD_REGISTRY}/gordo

if [[ $GITHUB_REF == refs/tags/* ]]; then
    VERSION=${GITHUB_REF#refs/tags/}
else
    VERSION=${GITHUB_SHA::8}
fi

IMAGE_TYPE="dev"
STABLE=""
if [ "$GITHUB_EVENT_NAME" == "release" ]; then
    IMAGE_TYPE="prod"
    prerelease=`cat "$GITHUB_EVENT_PATH" | jq -rM .release.prerelease`
    if [ "$prerelease" == "false" ]; then
        STABLE="true"
    fi
fi

function version_tags {
    image=$1
    version=(${2//./ })
    output=$image:$2
    if [ ${#version[@]} -ge 3 ]; then
        if [ -n "${version[0]}" ]; then
            output=$output,$image:${version[0]}
            if [ -n "${version[1]}" ]; then
                output=$output,$image:${version[0]}.${version[1]}
            fi
        fi
    fi
    echo $output
}

function set_output_tags {
    var_name=$1
    image_name=$2
    tags=$DOCKER_DEV_IMAGE/$image_name:latest,$(version_tags "$DOCKER_DEV_IMAGE/$image_name" "$VERSION")
    if [ "$IMAGE_TYPE" == "prod" ]; then
        tags=$tags,$DOCKER_PROD_IMAGE/$image_name:latest,$(version_tags "$DOCKER_PROD_IMAGE/$image_name" "$VERSION")
    fi
    if [ "$STABLE" == "true" ]; then
        tags=$tags,$DOCKER_DEV_IMAGE/$image_name:stable
        if [ "$IMAGE_TYPE" == "prod" ]; then
            tags=$tags,$DOCKER_PROD_IMAGE/$image_name:stable
        fi
    fi
    echo ::set-output name=$var_name::$tags
}

BASE_IMAGE=gordo_base

echo ::set-output name=version::${VERSION}
echo ::set-output name=stable::${STABLE}
echo ::set-output name=image_type::${IMAGE_TYPE}
echo ::set-output name=created::$(date -u +'%Y-%m-%dT%H:%M:%SZ')
echo ::set-output name=base_image::$BASE_IMAGE:$VERSION
set_output_tags "tags_gordo_client" "gordo-client"
set_output_tags "tags_gordo_deploy" "gordo-deploy"
set_output_tags "tags_gordo_model_builder" "gordo-model-builder"
set_output_tags "tags_gordo_model_server" "gordo-model-server"