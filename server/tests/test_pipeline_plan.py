from autochart import pipeline as PL
from autochart.frets import Note


def test_every_instrument_gets_a_source():
    silent = {"drums": False, "bass": False, "other": True, "vocals": False, "guitar": False, "piano": True}
    plan = PL.plan_sources(silent)
    assert set(plan) == {"guitar", "bass", "rhythm", "keys"}
    assert plan["guitar"] == ("guitar", "")
    assert plan["rhythm"][0] == "guitar" and plan["rhythm"][1] == "copy"      # other silencioso
    assert plan["keys"][0] == "vocals"


def test_only_one_stem_available():
    silent = {k: True for k in ("drums", "bass", "other", "vocals", "guitar", "piano")}
    silent["vocals"] = False
    plan = PL.plan_sources(silent)
    assert plan["guitar"] == ("vocals", "") and plan["rhythm"] == ("vocals", "copy")   # bass: lo deriva build_tracks


def test_variants():
    notes = [Note(0.0, 0.2, 60, .8), Note(0.0, 0.2, 40, .8), Note(0.26, 0.4, 62, .8)]
    s2t = lambda s: s * 384
    assert [n.pitch for n in PL.apply_variant(notes, "lowest", s2t)] == [40, 62]
    assert len(PL.apply_variant(notes, "copy", s2t)) == 3


def test_sparse_parts_are_rebuilt_from_guitar():
    from autochart import tempo
    g = tempo.Grid.build([0.5 * i for i in range(1, 80)])
    guitar = [Note(1.0 + 0.25 * i, 1.2 + 0.25 * i, 60 + (i * 7) % 12, .8) for i in range(120)]
    few = guitar[:12]                       # stem de respaldo casi vacío
    tracks, nps = PL.build_tracks({"guitar": (guitar, ""), "rhythm": (few, "copy"), "keys": (few, "copy")},
                                  [], g, set(), 60.0)
    n = lambda k: len(tracks[k]["Expert"].notes)
    assert {"guitar", "rhythm", "keys", "bass", "coop"} <= set(tracks)
    assert n("rhythm") >= 0.7 * n("guitar") and n("keys") >= 0.7 * n("guitar")
