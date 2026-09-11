"""Unit tests for Artifact Pipeline Router & Schema Validator (M25)."""

import pytest
from core.artifact_manager import ArtifactManager
from core.artifact_pipeline import (
    ArtifactPipelineRouter,
    ArtifactSchemaValidator,
    DataflowChannel,
)
from core.artifact_store import InMemoryArtifactStore
from core.artifact_types import Artifact, ArtifactType
from core.campaign_types import (
    ArtifactContract,
    DataflowBinding,
    DataflowChannelType,
)
from core.provenance import wrap_tainted, is_tainted


def test_schema_validator_valid_and_invalid_schemas():
    contract = ArtifactContract(
        contract_id="c_report",
        expected_artifact_type=ArtifactType.DOCUMENT,
        required_mime_types=("text/markdown",),
        schema_definition={
            "required_keys": ["summary", "word_count"],
            "field_types": {"summary": "str", "word_count": "int"},
        },
    )

    art = Artifact(
        artifact_id="art_1",
        name="report.md",
        artifact_type=ArtifactType.DOCUMENT,
        version=1,
        content_hash="a" * 64,
        size_bytes=100,
        mime_type="text/markdown",
        storage_uri="cas://1",
    )

    valid_content = {"summary": "Completed analysis", "word_count": 500}
    is_valid, err = ArtifactSchemaValidator.validate(art, valid_content, contract)
    assert is_valid
    assert err is None

    # Missing key
    invalid_content = {"summary": "Completed analysis"}
    is_valid, err = ArtifactSchemaValidator.validate(art, invalid_content, contract)
    assert not is_valid
    assert "missing required key 'word_count'" in err

    # Field type mismatch
    type_err_content = {"summary": "Completed analysis", "word_count": "not_an_int"}
    is_valid, err = ArtifactSchemaValidator.validate(art, type_err_content, contract)
    assert not is_valid
    assert "must be of type 'int'" in err


def test_schema_validator_taint_security_enforcement():
    strict_contract = ArtifactContract(
        contract_id="c_strict",
        expected_artifact_type=ArtifactType.DATASET,
        allow_tainted=False,
    )

    clean_art = Artifact(
        artifact_id="art_clean",
        name="clean.json",
        artifact_type=ArtifactType.DATASET,
        version=1,
        content_hash="b" * 64,
        size_bytes=50,
        mime_type="application/json",
        storage_uri="cas://2",
        taint_status=False,
    )
    is_valid, err = ArtifactSchemaValidator.validate(clean_art, {"data": 123}, strict_contract)
    assert is_valid

    tainted_art = Artifact(
        artifact_id="art_tainted",
        name="tainted.json",
        artifact_type=ArtifactType.DATASET,
        version=1,
        content_hash="c" * 64,
        size_bytes=50,
        mime_type="application/json",
        storage_uri="cas://3",
        taint_status=True,
    )
    is_valid, err = ArtifactSchemaValidator.validate(tainted_art, {"data": 123}, strict_contract)
    assert not is_valid
    assert "Tainted artifact rejected" in err

    # Tainted value inside clean manifest
    tainted_payload = wrap_tainted({"data": 123}, is_untrusted=True, source_type="external")
    is_valid, err = ArtifactSchemaValidator.validate(clean_art, tainted_payload, strict_contract)
    assert not is_valid
    assert "Tainted artifact rejected" in err


def test_artifact_pipeline_router_routing_and_retrieval():
    store = InMemoryArtifactStore()
    mgr = ArtifactManager(store=store)

    art1 = mgr.store_artifact(
        name="dataset_v1.json",
        content={"records": [1, 2, 3]},
        artifact_type=ArtifactType.DATASET,
        producer_goal_id="goal_extractor",
    )

    contract = ArtifactContract(
        contract_id="c_dataset",
        expected_artifact_type=ArtifactType.DATASET,
        min_quality_score=0.5,
    )

    binding = DataflowBinding(
        binding_id="b_extract_to_train",
        source_goal_id="goal_extractor",
        target_goal_id="goal_trainer",
        source_artifact_name="dataset_v1.json",
        target_input_key="training_data",
        contract=contract,
    )

    router = ArtifactPipelineRouter([binding])

    # Route goal output
    report = router.route_goal_artifacts(
        producer_goal_id="goal_extractor",
        artifact_ids=[art1.artifact_id],
        artifact_manager=mgr,
    )
    assert report["routed_count"] == 1
    assert len(report["deliveries"]) == 1

    # Consumer goal retrieves input
    consumer_inputs = router.get_input_artifacts_for_goal(
        consumer_goal_id="goal_trainer",
        artifact_manager=mgr,
    )
    assert "training_data" in consumer_inputs
    assert consumer_inputs["training_data"]["artifact_id"] == art1.artifact_id
    assert consumer_inputs["training_data"]["content"] == {"records": [1, 2, 3]}


def test_artifact_pipeline_router_wildcard_matching():
    store = InMemoryArtifactStore()
    mgr = ArtifactManager(store=store)

    art = mgr.store_artifact(
        name="any_file.txt",
        content="arbitrary text",
        artifact_type=ArtifactType.DOCUMENT,
        producer_goal_id="goal_A",
    )

    binding = DataflowBinding(
        binding_id="b_wildcard",
        source_goal_id="goal_A",
        target_goal_id="goal_B",
        source_artifact_name="*",
        target_input_key="input_doc",
    )

    router = ArtifactPipelineRouter([binding])
    report = router.route_goal_artifacts("goal_A", [art.artifact_id], mgr)
    assert report["routed_count"] == 1

    inputs = router.get_input_artifacts_for_goal("goal_B", mgr)
    assert "input_doc" in inputs


def test_dataflow_channel_buffering_and_bounds():
    binding = DataflowBinding(
        binding_id="b_buf",
        source_goal_id="g1",
        target_goal_id="g2",
        source_artifact_name="data.json",
        target_input_key="data",
    )
    channel = DataflowChannel(binding=binding, max_buffer_size=3)

    assert channel.push("art_1")
    assert channel.push("art_2")
    assert channel.push("art_3")
    assert channel.read_all() == ["art_1", "art_2", "art_3"]

    # Overflow drops oldest
    assert channel.push("art_4")
    assert channel.read_all() == ["art_2", "art_3", "art_4"]
    assert channel.peek_latest() == "art_4"
