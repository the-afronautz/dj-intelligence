# DJ Intelligence

Tools and insights to improve DJing skills, businesses, and workflows.

## Projects

### [`vinyl_analyzer/`](./vinyl_analyzer)

A small local web app that captures a snippet from your turntable speakers,
detects BPM and Camelot key, and logs results to a queryable SQLite library
(with one-click Excel export). Uses `librosa` for audio analysis: multi-band
onset detection with a tempo prior for BPM, Sha'ath profiles + window voting
for key. Built-in escape hatches (candidate picker, tap tempo, manual
override) for tracks that fool automatic detection.

See [`vinyl_analyzer/README.md`](./vinyl_analyzer/README.md) for setup,
architecture, and a file-by-file walkthrough.

## Data credits

Tempo and musical-key data in the catalogue lookup tool is sourced in part from
[GetSongBPM](https://getsongbpm.com), alongside MusicBrainz, AcousticBrainz and
Deezer.

## License

MIT — see [LICENSE](./LICENSE).

Contributions are welcome. Contributors sign a short
[CLA](./CLA.md) so the project can keep its licensing options open;
you retain copyright in everything you write.
