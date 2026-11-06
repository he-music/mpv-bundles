# libmpv bundle license materials

Each release bundle contains the exact source archives and license notices used for that target. mpv and FFmpeg are LGPL licensed; libass, FreeType/FTL, FriBidi, HarfBuzz, libplacebo, fast_float, Vulkan-Headers, and pkgconf retain their upstream license terms. The bundle is a replaceable shared library and must remain relinkable under the applicable LGPL terms.

The build disables mpv scripting and therefore does not bundle Lua. Windows toolchain notices are included when produced by the native MSYS2 job. Review `licenses/` and `sources/` in each release asset before redistribution. No proprietary or user audio files are included.
