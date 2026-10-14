"""Configuration/source QA using two wholly synthetic utility schemas.

Author: Lucas HT Nguyen
Contact: https://github.com/lucashtnguyen
Date: 2026-10-14
Manually maintained; fixtures are fictional and contain no project data.
"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from umdev.configuration import ConfigurationError, load_config, validate_config
from umdev.sources import SourceCapabilityError, SourceError, ingest_sources, matches_predicates, source_capabilities
from umdev.units import from_metres, to_metres

FIXTURES = Path(__file__).parent / "fixtures" / "schema_equivalence"


def load(letter="a"):
    return load_config(FIXTURES / f"utility_{letter}.config.json")


@pytest.mark.parametrize("unit,value,expected", [("ft",1,0.3048),("us_survey_ft",1,1200/3937),("mm",300,0.3),("in",12,0.3048),("m",-1,-1)])
def test_known_unit_hand_calculations(unit, value, expected):
    assert to_metres(value,unit) == pytest.approx(expected,abs=1e-14)
    assert from_metres(expected,unit) == pytest.approx(value)


@pytest.mark.parametrize("value", [True,None,"nan","inf","1_000","ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â‚¬Å¾Ã‚Â¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¡",float("nan"),float("inf")])
def test_units_reject_invalid_values(value):
    with pytest.raises(ValueError): to_metres(value,"ft")


def test_survey_and_international_foot_remain_distinct():
    assert to_metres(1,"ft") != to_metres(1,"us_survey_ft")
    with pytest.raises(ValueError): to_metres(1,"feet")


def test_two_schemas_normalize_same_network_by_configuration():
    first,second = ingest_sources(load()),ingest_sources(load("b"))
    for field in ["asset_id","from_node","to_node","material","status","pipe_kind"]:
        assert first["pipes"][0][field] == second["pipes"][0][field]
    for field in ["diameter","length"]:
        assert first["pipes"][0][field] == pytest.approx(second["pipes"][0][field])
    assert first["pipes"][0]["coordinates"] == second["pipes"][0]["coordinates"]
    for a,b in zip(first["nodes"],second["nodes"]):
        assert a["asset_id"] == b["asset_id"]
        assert a["floor"] == pytest.approx(b["floor"])
        assert a["rim"] == pytest.approx(b["rim"])
    assert [p["structure_id"] for p in second["ports"]] == ["001","002"]
    assert second["pipes"][0]["asset_id"] == "0007"
    assert second["pipes"][0]["raw_geometry"]["paths"][0][0][2] == 999
    assert second["pipes"][0]["source_attributes"]["MAT_CODE"] == 17
    assert second["pipes"][0]["measurement_provenance"]["diameter"]["source_unit"] == "in"
    assert not first["findings"] and not second["findings"]


def test_overlay_paths_use_declaration_directory_and_lists_replace(tmp_path):
    fragments=tmp_path/"fragments"; fragments.mkdir()
    base=load().data
    base["sources"]["network"]["path"]="data/network.json"
    (fragments/"base.json").write_text(json.dumps(base))
    (tmp_path/"project.json").write_text(json.dumps({"extends":["fragments/base.json"],"ports":{"observations":[]},"review":{"overrides_path":None}}))
    resolved=load_config(tmp_path/"project.json")
    assert Path(resolved.data["sources"]["network"]["path"]) == fragments/"data/network.json"
    assert resolved.data["ports"]["observations"] == []
    assert resolved.origins["/sources/network/path"] == str(fragments/"base.json")
    assert resolved.data["review"]["overrides_path"] is None
    assert resolved.sha256 == load_config(tmp_path/"project.json").sha256
    copy=resolved.to_dict();copy["kind"]="changed"
    assert resolved.data["kind"] == "umdev_project"


def test_ordered_extends_right_then_project_wins(tmp_path):
    base=load().data
    (tmp_path/"base.json").write_text(json.dumps(base))
    (tmp_path/"one.json").write_text(json.dumps({"metadata":{"name":"one"}}))
    (tmp_path/"two.json").write_text(json.dumps({"metadata":{"name":"two"}}))
    (tmp_path/"project.json").write_text(json.dumps({"extends":["base.json","one.json","two.json"]}))
    assert load_config(tmp_path/"project.json").data["metadata"]["name"] == "two"


def test_cycle_and_missing_extends_are_explicit_errors(tmp_path):
    a=tmp_path/"a.json"; b=tmp_path/"b.json"
    a.write_text('{"extends":["b.json"]}');b.write_text('{"extends":["a.json"]}')
    with pytest.raises(ConfigurationError,match="cycle"): load_config(a)
    a.write_text('{"extends":["missing.json"]}')
    with pytest.raises(ConfigurationError,match="Cannot read"): load_config(a)


@pytest.mark.parametrize("edit", [
    lambda c:c.update(schema_version=2),lambda c:c.update(typo={}),
    lambda c:c["sources"]["network"].update(read_only=False),
    lambda c:c["datasets"]["pipes"].update(source="missing"),
    lambda c:c["datasets"]["pipes"]["fields"]["diameter"].update(unit="furlong"),
    lambda c:c["datasets"]["pipes"]["fields"]["diameter"].update(typo=True),
    lambda c:c["ports"]["observations"][0].update(parser="python_eval"),
    lambda c:c["ports"]["observations"][0].update(field="not_mapped"),
    lambda c:c["selection"].update(scenario="unknown"),
    lambda c:c["elevations"]["transforms"][0].update(target_datum="OTHER"),
    lambda c:c["roughness"]["rules"].update(OTHER={"aliases":["PVC"],"value":0.014}),
    lambda c:c["roughness"]["rules"]["PVC"].update(reference_range=[0.014,0.015]),
    lambda c:c["publication"].update(overwrite_sources=True),
    lambda c:c["parameters"]["nodes"].update(estimators=["invented"]),
    lambda c:c["selection"]["scope"].update(method="polygon_not_implemented"),
    lambda c:c["ports"]["assignment"].update(diameter_tolerance={"value":-1,"unit":"m"}),
])
def test_invalid_configuration_rejected(edit):
    config=load().to_dict();edit(config)
    with pytest.raises(ConfigurationError): validate_config(config)


def mutate_packet(tmp_path,edit):
    config=load().to_dict()
    packet=json.loads(Path(config["sources"]["network"]["path"]).read_text())
    edit(packet)
    path=tmp_path/"network.json";path.write_text(json.dumps(packet))
    config["sources"]["network"]["path"]=str(path)
    return config


def pipe(packet):return packet["datasets"]["GravitySegments"]["records"][0]


def test_missing_mapping_is_schema_error(tmp_path):
    config=mutate_packet(tmp_path,lambda p:pipe(p).pop("bore_mm"))
    with pytest.raises(SourceError,match="missing mapped"): ingest_sources(config)


def test_duplicate_identity_is_error(tmp_path):
    config=mutate_packet(tmp_path,lambda p:p["datasets"]["GravitySegments"]["records"].append(deepcopy(pipe(p))))
    with pytest.raises(SourceError,match="Duplicate"):ingest_sources(config)


@pytest.mark.parametrize("kind", ["crs","units","unknown"])
def test_geometry_metadata_mismatch_never_relabels(tmp_path,kind):
    def edit(packet):
        metadata=packet["datasets"]["GravitySegments"]["metadata"]
        if kind=="crs":metadata["crs"]={"epsg":4326}
        elif kind=="units":metadata["linear_unit"]="ft"
        else:metadata.pop("crs")
    with pytest.raises(SourceError,match="CRS|linear unit"):
        ingest_sources(mutate_packet(tmp_path,edit))


@pytest.mark.parametrize("shape,flag", [({"paths":[[[0,0],[1,1]],[[2,2],[3,3]]]},"unsupported_multipart_geometry"),({"curvePaths":[]},"unsupported_curved_geometry"),({"paths":[[[0,0],[0,0]]]},"degenerate_line_geometry"),({"paths":[[[0,0],[1,"nan"]]]},"nonfinite_or_invalid_geometry")])
def test_bad_geometry_retained_as_findings_not_dropped(tmp_path,shape,flag):
    config=mutate_packet(tmp_path,lambda p:pipe(p).update(shape=shape))
    result=ingest_sources(config)
    assert len(result["pipes"])==1 and result["pipes"][0]["coordinates"] is None
    assert flag in [f["code"] for f in result["findings"]]


def test_invalid_numbers_and_unmapped_domains_are_findings(tmp_path):
    config=mutate_packet(tmp_path,lambda p:pipe(p).update(bore_mm="unknown"))
    result=ingest_sources(config)
    assert result["pipes"][0]["diameter"] is None
    assert result["findings"][0]["code"]=="invalid_numeric_measurement"


def test_zero_missing_is_field_specific_and_sentinels_numeric(tmp_path):
    config=mutate_packet(tmp_path,lambda p:pipe(p).update(bore_mm="-999",survey_m=0))
    config["datasets"]["pipes"]["fields"]["diameter"]["missing"]=[-999]
    result=ingest_sources(config)
    assert result["pipes"][0]["diameter"] is None
    assert result["pipes"][0]["length"]==0
    config["datasets"]["pipes"]["fields"]["length"]["zero_is_missing"]=True
    assert ingest_sources(config)["pipes"][0]["length"] is None


def test_native_drivers_have_actionable_diagnostic():
    config=load().to_dict();config["sources"]["network"]["format"]="geopackage"
    with pytest.raises(SourceCapabilityError,match="exported JSON"):ingest_sources(config)
    assert source_capabilities()["file_geodatabase"]["available"] is False


def test_source_hash_and_input_preservation():
    config=load();path=Path(config.data["sources"]["network"]["path"]);before=path.read_bytes()
    first,second=ingest_sources(config),ingest_sources(config)
    assert first==second and path.read_bytes()==before
    assert len(first["manifest"]["network"]["sha256"])==64


def test_restricted_predicates():
    assert matches_predicates({"status":"active","diameter":0.3},[{"field":"status","operator":"in","values":["active"]},{"field":"diameter","operator":"gte","value":12,"unit":"in"}]) is False
    assert matches_predicates({"v":None},[{"field":"v","operator":"gt","value":0}]) is False
    with pytest.raises(SourceError):matches_predicates({},[{"field":"missing","operator":"equals","value":1}])



def test_record_fingerprint_changes_when_evidence_changes(tmp_path):
    first=ingest_sources(load())["pipes"][0]
    updated=ingest_sources(mutate_packet(tmp_path,lambda p:pipe(p).update(wall="CONCRETE")))["pipes"][0]
    assert len(first["source_record_sha256"])==64
    assert first["key"]==updated["key"]
    assert first["source_record_sha256"]!=updated["source_record_sha256"]


def test_duplicate_json_keys_rejected(tmp_path):
    path=tmp_path/"duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":2}')
    with pytest.raises(ConfigurationError,match="Duplicate JSON"):load_config(path)


@pytest.mark.parametrize("edit", [lambda c:c["ports"].update(observations={}),lambda c:c["datasets"]["pipes"].update(fields=[]),lambda c:c["roughness"].update(rules=[]),lambda c:c["selection"].update(explicit_pipe_keys="pipe"),lambda c:c["selection"].update(scope={"method":"bounds","bounds":[1,2,0,3]})])
def test_malformed_nested_contract_is_configuration_error(edit):
    config=load().to_dict();edit(config)
    with pytest.raises(ConfigurationError):validate_config(config)



def test_arbitrary_described_port_fields_support_three_incoming_and_one_outgoing(tmp_path):
    from umdev.parameters import prepare_network_from_dict

    config=load().to_dict()
    node_fields=config["datasets"]["structures"]["fields"]
    node_fields.pop("inlet_description");node_fields.pop("outlet_description")
    # Different utility schemas need only config mappings, never numbered fields
    # baked into the reusable canonical contract or port-assignment algorithm.
    observations=[]
    for canonical,source,role in [("entry_alpha","NotesA","in"),("entry_beta","NotesZ","in"),("entry_gamma","DropSchedule","in"),("exit_schedule","FlowingAway","out")]:
        node_fields[canonical]={"source":source,"type":"string"}
        observations.append({"id":canonical,"dataset":"structures","field":canonical,
                             "parser":"elevation_direction_diameter_v1","role":role,
                             "units":{"elevation":"m","diameter":"mm"}})
    config["ports"]["observations"]=observations
    points={"001":[-10,10],"002":[0,10],"003":[10,10],"100":[0,0],"200":[0,-10]}
    pipes=[]
    for identity,up,down,size in [("A","001","100",200),("B","002","100",250),("C","003","100",300),("D","100","200",400)]:
        pipes.append({"segment_text":identity,"start_access":up,"finish_access":down,
                      "bore_mm":size,"survey_m":10,"wall":"PVC","service":"active",
                      "type":"gravity","shape":{"type":"LineString","coordinates":[points[up],points[down]]}})
    nodes=[]
    for identity,xy in points.items():
        floor=9 if identity=="100" else 8 if identity=="200" else 10
        node={"access_text":identity,"base_m":floor,"cover_m":floor+2,"basis":"SYNTHETIC_DATUM",
              "NotesA":None,"NotesZ":None,"DropSchedule":None,"FlowingAway":None,
              "shape":{"type":"Point","coordinates":xy}}
        if identity=="100":node.update(NotesA="9.1 m, 200 mm",NotesZ="9.2 m, 250 mm",DropSchedule="9.3 m, 300 mm",FlowingAway="9 m, 400 mm")
        elif identity=="200":node["NotesA"]="8 m, 400 mm"
        else:node["FlowingAway"]=f"10 m, {dict(A=200,B=250,C=300)[{'001':'A','002':'B','003':'C'}[identity]]} mm"
        nodes.append(node)
    packet={"datasets":{"GravitySegments":{"records":pipes,"metadata":{"crs":{"epsg":3857},"linear_unit":"m"}},"AccessCovers":{"records":nodes,"metadata":{"crs":{"epsg":3857},"linear_unit":"m"}}}}
    path=tmp_path/"different_utility.json";path.write_text(json.dumps(packet))
    config["sources"]["network"]["path"]=str(path)
    validate_config(config)
    canonical=ingest_sources(config)
    center=next(n for n in canonical["nodes"] if n["asset_id"]=="100")
    assert center["entry_beta"]=="9.2 m, 250 mm"
    prepared=prepare_network_from_dict(canonical,config).to_dict()
    assert len(prepared["pipes"])==4
    assert not prepared["held"]
    by_id={pipe["asset_id"]:pipe for pipe in prepared["pipes"]}
    assert by_id["A"]["outlet_offset_m"]==pytest.approx(0.1)
    assert by_id["B"]["outlet_offset_m"]==pytest.approx(0.2)
    assert by_id["C"]["outlet_offset_m"]==pytest.approx(0.3)
    assert by_id["D"]["inlet_offset_m"]==pytest.approx(0)


def test_extra_inline_field_must_be_explicitly_referenced_and_text():
    config=load().to_dict()
    fields=config["datasets"]["structures"]["fields"]
    fields["arbitrary_note"]={"source":"RawNote","type":"string"}
    with pytest.raises(ConfigurationError,match="Unknown canonical"):validate_config(config)
    config["ports"]["observations"].append({"id":"extra","dataset":"structures","field":"arbitrary_note","parser":"elevation_direction_diameter_v1","role":"in","units":{"elevation":"m","diameter":"mm"}})
    validate_config(config)
    fields["arbitrary_note"]={"source":"RawNote","type":"number","unit":"m"}
    with pytest.raises(ConfigurationError,match="must map text"):validate_config(config)



@pytest.mark.parametrize("disposition", ["review","hold"])
def test_configured_qa_dispositions_validate(disposition):
    config=load().to_dict()
    config["qa"]["rules"]={"adverse_gravity_grade":{"severity":"warning","disposition":disposition}}
    validate_config(config)


def test_unsupported_qa_disposition_is_explicit_error():
    config=load().to_dict()
    config["qa"]["rules"]={"pipe_crown_above_rim":{"severity":"warning","disposition":"silently_exclude"}}
    with pytest.raises(ConfigurationError,match="disposition"):validate_config(config)



@pytest.mark.parametrize("epsg", [4326,4269,4978,4979])
def test_nonlinear_or_nonplanar_crs_cannot_be_relabelled_metres(tmp_path,epsg):
    config=mutate_packet(tmp_path,lambda p:p["datasets"]["GravitySegments"]["metadata"].update(crs={"epsg":epsg}))
    config["datasets"]["pipes"]["geometry"]["declared_crs"]={"epsg":epsg}
    with pytest.raises(SourceError,match="projected two-dimensional linear CRS"):
        ingest_sources(config)


def test_projected_crs_actual_axis_units_are_validated(tmp_path):
    config=mutate_packet(tmp_path,lambda p:p["datasets"]["GravitySegments"]["metadata"].update(linear_unit="ft"))
    config["datasets"]["pipes"]["geometry"]["linear_unit"]="ft"
    with pytest.raises(SourceError,match="conflicts with CRS.*metre"):
        ingest_sources(config)


def test_projected_international_foot_crs_is_supported_and_survey_foot_distinct(tmp_path):
    def edit(packet):
        for dataset in packet["datasets"].values():
            dataset["metadata"]={"crs":{"epsg":2913},"linear_unit":"ft"}
    config=mutate_packet(tmp_path,edit)
    for dataset in config["datasets"].values():
        dataset["geometry"].update(declared_crs={"epsg":2913},linear_unit="ft")
    canonical=ingest_sources(config)
    assert canonical["pipes"][0]["coordinates"][-1]==pytest.approx([0,3.048])
    assert canonical["pipes"][0]["geometry_metadata"]["crs_validation"]=="PROJ_projected_2D_axes_and_units"
    packet=json.loads(Path(config["sources"]["network"]["path"]).read_text())
    for dataset in packet["datasets"].values():dataset["metadata"]["linear_unit"]="us_survey_ft"
    Path(config["sources"]["network"]["path"]).write_text(json.dumps(packet))
    for dataset in config["datasets"].values():dataset["geometry"]["linear_unit"]="us_survey_ft"
    with pytest.raises(SourceError,match="conflicts with CRS"):
        ingest_sources(config)


def test_missing_authoritative_crs_inspection_has_capability_diagnostic(monkeypatch):
    import builtins
    from umdev.sources import _projected_crs
    original=builtins.__import__
    def block_pyproj(name,*args,**kwargs):
        if name=="pyproj" or name.startswith("pyproj."):raise ImportError("synthetic unavailable dependency")
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,"__import__",block_pyproj)
    with pytest.raises(SourceCapabilityError,match="declared pyproj dependency"):
        _projected_crs({"epsg":3857},"m")
