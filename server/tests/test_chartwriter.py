from autochart.chartwriter import Track, render
from autochart.playability import ChartNote, DIFFICULTIES


def test_snapshot():
    notes = [ChartNote(0, (0,), sustain=0), ChartNote(192, (1, 2), sustain=96), ChartNote(384, (4,))]
    tracks = {"guitar": {"Expert": Track(notes, [(0, 384)])}}
    text = render({"title": 'Hola "x"', "artist": "Yo"}, [(0, 120000), (192, 120000)], tracks)
    assert text == '''[Song]
{
  Name = "Hola 'x'"
  Artist = "Yo"
  Charter = "Switch Hero Autochart"
  Offset = 0
  Resolution = 192
  MusicStream = "song.ogg"
}
[SyncTrack]
{
  0 = TS 4
  0 = B 120000
}
[Events]
{
}
[ExpertSingle]
{
  0 = S 2 384
  0 = N 0 0
  192 = N 1 96
  192 = N 2 96
  384 = N 4 0
}
'''
