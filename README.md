# he-music/mpv-bundles

Independent release repository for the audio-only libmpv 0.42.0 bundle used by HE-Music. The host application downloads a release asset; this repository never stores binary libraries in Git.

Targets are `darwin/universal`, `windows/amd64`, `windows/arm64`, `linux/amd64`, and `linux/arm64`. The recipe pins mpv 0.42.0/client API 2.6, FFmpeg 8.0.1, all source URLs and SHA256 values in [`sources.lock.json`](scripts/mpv/sources.lock.json). FFmpeg is configured with the `dst` decoder as well as DSD decoders, so compressed DSDIFF/DFF can be decoded. `libmpv` is audio-only: video, GPU, hardware decode, encoders and scripts are disabled while the native audio boundary remains CoreAudio, WASAPI, or PulseAudio.

## Local use

```sh
python3 -m venv build/tools
build/tools/bin/pip install -r requirements-build.txt
python3 scripts/mpv/prepare.py --fetch-only --offline
python3 scripts/mpv/prepare.py --target darwin/universal
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/mpv/*.py
```

Linux builds run natively in the locked Ubuntu 22.04 digest container and require `libpulse-dev`; Windows builds run in native MSYS2 `CLANG64` or `CLANGARM64`; macOS builds compile two SDK slices and merge them with `lipo`. Do not use QEMU to claim an arm64 runtime result. `--check` validates an existing prepared bundle without downloading or compiling.

## Regression entry points

`python3 scripts/mpv/smoke.py --resources <bundle>` generates temporary WAV, DSF, and uncompressed DFF samples and uses `ao=null`. For a real compressed DST DFF regression, provide a locally obtained sample with `--dst-sample /path/to/sample.dst.dff`; the file is read only and is never copied into the repository or bundle. The check requires the selected codec to be `dst`. The test proves decode/control behavior, not audible output or device compatibility.

Each bundle manifest records target, recipe digest, file SHA256/size, dynamic dependencies, build information, source archives, and runtime validation. Release assets are created as draft releases and should be reviewed before publishing. `licenses/` contains notices and `sources/` contains corresponding source archives; see [`LICENSES.md`](LICENSES.md) for LGPL/MPL-style redistribution guidance.
