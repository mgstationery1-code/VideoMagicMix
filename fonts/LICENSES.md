# VideoMagicMix Fonts

The Windows release bundles **Source Han Sans SC Regular**, under SIL OFL 1.1.
The build downloads `SourceHanSansSC-Regular.otf` and its unmodified license
`SourceHanSans-LICENSE.txt` from the official Adobe repository:

https://github.com/adobe-fonts/source-han-sans/tree/a4f7cf94edfb9d7ffbdfc4841de276358bd7e0f2

The font is verified against Git blob `8113ad53da18b6a08b133c6c460ae1d818a0f865`.
Runtime uses the internal family name `Source Han Sans SC` for ASS rendering.
Only fonts with an explicit redistribution license may be added here.

Recommended sources are the official Noto CJK, Source Han, and LXGW WenKai
repositories. Keep the original license file next to each bundled font and add
the exact source URL and version in this document before packaging.

The application also reads Windows-installed fonts at runtime. Windows system
fonts are not copied into this directory.
