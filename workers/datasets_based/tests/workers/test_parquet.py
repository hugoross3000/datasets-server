# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 The HuggingFace Authors.

import io
from http import HTTPStatus
from typing import Iterator, List

import pandas as pd
import pytest
import requests
from libcommon.exceptions import CustomError
from libcommon.simple_cache import DoesNotExist, get_response

from datasets_based.config import AppConfig, ParquetConfig
from datasets_based.workers.parquet import (
    DatasetInBlockListError,
    DatasetTooBigFromDatasetsError,
    DatasetTooBigFromHubError,
    ParquetWorker,
    get_dataset_info_or_raise,
    parse_repo_filename,
    raise_if_blocked,
    raise_if_not_supported,
    raise_if_too_big_from_datasets,
    raise_if_too_big_from_hub,
)

from ..fixtures.hub import HubDatasets


# see https://github.com/pytest-dev/pytest/issues/363#issuecomment-406536200
@pytest.fixture(scope="module", autouse=True)
def set_supported_datasets(hub_datasets: HubDatasets) -> Iterator[pytest.MonkeyPatch]:
    mp = pytest.MonkeyPatch()
    mp.setenv(
        "PARQUET_BLOCKED_DATASETS",
        ",".join(value["name"] for value in hub_datasets.values() if "jsonl" in value["name"]),
    )
    mp.setenv(
        "PARQUET_SUPPORTED_DATASETS",
        ",".join(value["name"] for value in hub_datasets.values() if "big" not in value["name"]),
    )
    yield mp
    mp.undo()


@pytest.fixture
def parquet_config(set_env_vars: pytest.MonkeyPatch, set_supported_datasets: pytest.MonkeyPatch) -> ParquetConfig:
    return ParquetConfig.from_env()


def get_worker(
    dataset: str,
    app_config: AppConfig,
    parquet_config: ParquetConfig,
    force: bool = False,
) -> ParquetWorker:
    return ParquetWorker(
        job_info={
            "type": ParquetWorker.get_job_type(),
            "dataset": dataset,
            "config": None,
            "split": None,
            "job_id": "job_id",
            "force": force,
        },
        app_config=app_config,
        parquet_config=parquet_config,
    )


def test_compute(app_config: AppConfig, parquet_config: ParquetConfig, hub_datasets: HubDatasets) -> None:
    dataset = hub_datasets["public"]["name"]
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is True
    cached_response = get_response(kind=worker.processing_step.cache_kind, dataset=dataset)
    assert cached_response["http_status"] == HTTPStatus.OK
    assert cached_response["error_code"] is None
    assert cached_response["worker_version"] == worker.get_version()
    assert cached_response["dataset_git_revision"] is not None
    content = cached_response["content"]
    assert len(content["parquet_files"]) == 1
    assert content == hub_datasets["public"]["parquet_response"]


def test_doesnotexist(app_config: AppConfig, parquet_config: ParquetConfig) -> None:
    dataset = "doesnotexist"
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is False
    with pytest.raises(DoesNotExist):
        get_response(kind=worker.processing_step.cache_kind, dataset=dataset)


@pytest.mark.parametrize(
    "dataset,blocked,raises",
    [
        ("public", ["public"], True),
        ("public", ["public", "audio"], True),
        ("public", ["audio"], False),
        ("public", [], False),
    ],
)
def test_raise_if_blocked(dataset: str, blocked: List[str], raises: bool) -> None:
    if raises:
        with pytest.raises(DatasetInBlockListError):
            raise_if_blocked(dataset=dataset, blocked_datasets=blocked)
    else:
        raise_if_blocked(dataset=dataset, blocked_datasets=blocked)


@pytest.mark.parametrize(
    "name,raises",
    [("public", False), ("big", True)],
)
def test_raise_if_too_big_from_hub(
    hub_datasets: HubDatasets, name: str, raises: bool, app_config: AppConfig, parquet_config: ParquetConfig
) -> None:
    dataset = hub_datasets[name]["name"]
    dataset_info = get_dataset_info_or_raise(
        dataset=dataset,
        hf_endpoint=app_config.common.hf_endpoint,
        hf_token=app_config.common.hf_token,
        revision="main",
    )
    if raises:
        with pytest.raises(DatasetTooBigFromHubError):
            raise_if_too_big_from_hub(dataset_info=dataset_info, max_dataset_size=parquet_config.max_dataset_size)
    else:
        raise_if_too_big_from_hub(dataset_info=dataset_info, max_dataset_size=parquet_config.max_dataset_size)


@pytest.mark.parametrize(
    "name,raises",
    [("public", False), ("big", True)],
)
def test_raise_if_too_big_from_datasets(
    hub_datasets: HubDatasets, name: str, raises: bool, app_config: AppConfig, parquet_config: ParquetConfig
) -> None:
    dataset = hub_datasets[name]["name"]
    if raises:
        with pytest.raises(DatasetTooBigFromDatasetsError):
            raise_if_too_big_from_datasets(
                dataset=dataset,
                hf_endpoint=app_config.common.hf_endpoint,
                hf_token=app_config.common.hf_token,
                revision="main",
                max_dataset_size=parquet_config.max_dataset_size,
            )
    else:
        raise_if_too_big_from_datasets(
            dataset=dataset,
            hf_endpoint=app_config.common.hf_endpoint,
            hf_token=app_config.common.hf_token,
            revision="main",
            max_dataset_size=parquet_config.max_dataset_size,
        )


@pytest.mark.parametrize(
    "in_list,raises",
    [
        (True, False),
        (False, True),
    ],
)
def test_raise_if_not_supported(
    hub_public_big: str, app_config: AppConfig, parquet_config: ParquetConfig, in_list: bool, raises: bool
) -> None:
    if raises:
        with pytest.raises(DatasetTooBigFromDatasetsError):
            raise_if_not_supported(
                dataset=hub_public_big,
                hf_endpoint=app_config.common.hf_endpoint,
                hf_token=app_config.common.hf_token,
                committer_hf_token=parquet_config.committer_hf_token,
                revision="main",
                max_dataset_size=parquet_config.max_dataset_size,
                supported_datasets=[hub_public_big] if in_list else ["another_dataset"],
                blocked_datasets=[],
            )
    else:
        raise_if_not_supported(
            dataset=hub_public_big,
            hf_endpoint=app_config.common.hf_endpoint,
            hf_token=app_config.common.hf_token,
            committer_hf_token=parquet_config.committer_hf_token,
            revision="main",
            max_dataset_size=parquet_config.max_dataset_size,
            supported_datasets=[hub_public_big] if in_list else ["another_dataset"],
            blocked_datasets=[],
        )


def test_not_supported_if_big(app_config: AppConfig, parquet_config: ParquetConfig, hub_public_big: str) -> None:
    # Not in the list of supported datasets and bigger than the maximum size
    dataset = hub_public_big
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is False
    cached_response = get_response(kind=worker.processing_step.cache_kind, dataset=dataset)
    assert cached_response["http_status"] == HTTPStatus.NOT_IMPLEMENTED
    assert cached_response["error_code"] == "DatasetTooBigFromDatasetsError"


def test_supported_if_gated(app_config: AppConfig, parquet_config: ParquetConfig, hub_gated_csv: str) -> None:
    # Access should must be granted
    dataset = hub_gated_csv
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is True
    cached_response = get_response(kind=worker.processing_step.cache_kind, dataset=dataset)
    assert cached_response["http_status"] == HTTPStatus.OK
    assert cached_response["error_code"] is None


def test_not_supported_if_gated_with_extra_fields(
    app_config: AppConfig, parquet_config: ParquetConfig, hub_gated_extra_fields_csv: str
) -> None:
    # Access request should fail because extra fields in gated datasets are not supported
    dataset = hub_gated_extra_fields_csv
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is False
    cached_response = get_response(kind=worker.processing_step.cache_kind, dataset=dataset)
    assert cached_response["http_status"] == HTTPStatus.NOT_FOUND
    assert cached_response["error_code"] == "GatedExtraFieldsError"


def test_blocked(app_config: AppConfig, parquet_config: ParquetConfig, hub_public_jsonl: str) -> None:
    # In the list of blocked datasets
    dataset = hub_public_jsonl
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    assert worker.process() is False
    cached_response = get_response(kind=worker.processing_step.cache_kind, dataset=dataset)
    assert cached_response["http_status"] == HTTPStatus.NOT_IMPLEMENTED
    assert cached_response["error_code"] == "DatasetInBlockListError"


@pytest.mark.parametrize(
    "name",
    ["public", "audio", "gated"],
)
def test_compute_splits_response_simple_csv_ok(
    hub_datasets: HubDatasets, name: str, app_config: AppConfig, parquet_config: ParquetConfig, data_df: pd.DataFrame
) -> None:
    dataset = hub_datasets[name]["name"]
    expected_parquet_response = hub_datasets[name]["parquet_response"]
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    result = worker.compute()
    assert result == expected_parquet_response

    # download the parquet file and check that it is valid
    if name == "audio":
        return

    if name == "public":
        df = pd.read_parquet(result["parquet_files"][0]["url"], engine="auto")
    else:
        # in all these cases, the parquet files are not accessible without a token
        with pytest.raises(Exception):
            pd.read_parquet(result["parquet_files"][0]["url"], engine="auto")
        r = requests.get(
            result["parquet_files"][0]["url"], headers={"Authorization": f"Bearer {app_config.common.hf_token}"}
        )
        assert r.status_code == HTTPStatus.OK, r.text
        df = pd.read_parquet(io.BytesIO(r.content), engine="auto")
    assert df.equals(data_df), df


@pytest.mark.parametrize(
    "name,error_code,cause",
    [
        ("empty", "EmptyDatasetError", "EmptyDatasetError"),
        ("does_not_exist", "DatasetNotFoundError", None),
        ("gated_extra_fields", "GatedExtraFieldsError", None),
        ("private", "DatasetNotFoundError", None),
    ],
)
def test_compute_splits_response_simple_csv_error(
    hub_datasets: HubDatasets,
    name: str,
    error_code: str,
    cause: str,
    app_config: AppConfig,
    parquet_config: ParquetConfig,
) -> None:
    dataset = hub_datasets[name]["name"]
    worker = get_worker(dataset=dataset, app_config=app_config, parquet_config=parquet_config)
    with pytest.raises(CustomError) as exc_info:
        worker.compute()
    assert exc_info.value.code == error_code
    if cause is None:
        assert exc_info.value.disclose_cause is False
        assert exc_info.value.cause_exception is None
    else:
        assert exc_info.value.disclose_cause is True
        assert exc_info.value.cause_exception == cause
        response = exc_info.value.as_response()
        assert set(response.keys()) == {"error", "cause_exception", "cause_message", "cause_traceback"}
        response_dict = dict(response)
        # ^ to remove mypy warnings
        assert response_dict["cause_exception"] == cause
        assert isinstance(response_dict["cause_traceback"], list)
        assert response_dict["cause_traceback"][0] == "Traceback (most recent call last):\n"


@pytest.mark.parametrize(
    "filename,split,config,raises",
    [
        ("config/builder-split.parquet", "split", "config", False),
        ("config/builder-split-00000-of-00001.parquet", "split", "config", False),
        ("builder-split-00000-of-00001.parquet", "split", "config", True),
        ("config/builder-not-supported.parquet", "not-supported", "config", True),
    ],
)
def test_parse_repo_filename(filename: str, split: str, config: str, raises: bool) -> None:
    if raises:
        with pytest.raises(Exception):
            parse_repo_filename(filename)
    else:
        assert parse_repo_filename(filename) == (config, split)
