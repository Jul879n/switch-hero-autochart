from autochart import pipeline as PL
from autochart.frets import Note


def test_every_instrument_gets_a_source():
    silent = {"drums": False, "bass": False, "other": True, "vocals": False, "guitar": False, "piano": True}
    plan = PL.plan_sources(silent)
    assert set(plan) == {"guitar", "bass", "rhythm", "keys"}
    assert plan["guitar"] == ("guitar", "")
    assert plan["rhythm"][0] == "guitar" and plan["rhythm"][1] == "sparse"      # other silencioso
    assert plan["keys"][0] == "vocals"


def test_only_one_stem_available():
    silent = {k: True for k in ("drums", "bass", "other", "vocals", "guitar", "piano")}
    silent["vocals"] = False
    plan = PL.plan_sources(silent)
    assert plan["guitar"] == ("vocals", "") and plan["rhythm"] == ("vocals", "sparse")   # bass: lo deriva build_tracks


def test_variants():
    notes = [Note(0.0, 0.2, 60, .8), Note(0.0, 0.2, 40, .8), Note(0.2, 0.4, 62, .8)]
    s2t = lambda s: s * 384
    assert [n.pitch for n in PL.apply_variant(notes, "lowest", s2t)] == [40, 62]
    assert [n.pitch for n in PL.apply_variant(notes, "sparse", s2t)] == [60, 40]   # 0.2 s = tick 77: fuera del pulso
