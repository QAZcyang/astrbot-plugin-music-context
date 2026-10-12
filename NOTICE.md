# Third-party license boundaries

Music Context's project-owned code and documentation are licensed under the
[MIT License](LICENSE). The project license does not replace or extend to
third-party code, assets, dependencies, services or content; those retain their
own copyright notices, licenses and applicable terms.

## SVG icons

The eight Dashboard SVG icons are adapted from Lucide at commit
`70562c1ee1c4fdcf736fe97bc893fb8511927934`. They remain under ISC, with
Feather-derived MIT notices also applying to music.svg and search.svg.
Retain both the [per-icon source notice](pages/dashboard/assets/icons/NOTICE.md)
and the [complete upstream license](pages/dashboard/assets/icons/LICENSE.txt)
when redistributing these resources. This root notice does not replace them.

## Frontend resources

Project-owned HTML, CSS and JavaScript use the project MIT License. Third-party
frontend assets and host-provided interfaces retain their original terms.
The current Dashboard uses local SVG assets and AstrBot's native bridge; it has
no bundled frontend framework, CDN resource or font file. System fonts are
provided by the user's environment. Historical validation screenshots may
contain host interfaces and earlier assets; the project MIT License does not
grant rights to those third-party elements.

## Python and host dependencies

AstrBot, CPython, the standard library, host-provided Python packages and
development tools remain under their respective upstream licenses. They are
not relicensed by Music Context's MIT License. The plugin does not bundle the
host, interpreter or these packages. Reviewed versions, licenses, upstream
links and deployment-inventory limits are recorded in the
[dependency audit](docs/REFERENCE.md). Distribution with a runtime must
retain the applicable upstream licenses and notices for that runtime.

## External content and references

Music metadata, lyrics, translations, platform services and third-party
research references are outside the project MIT grant. Source references and
reference scope is described in the [source inventory](docs/REFERENCE.md)
and [license review](docs/REFERENCE.md). The
[release content policy](docs/REFERENCE.md) continues to apply.
