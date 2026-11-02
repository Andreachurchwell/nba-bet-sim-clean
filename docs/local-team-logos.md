# Optional local team images

The private local installation uses 30 user-collected NBA team images already
present in `assets/`. Nothing is downloaded from ESPN, BALLDONTLIE, or another
image service. Rendering embeds validated local PNG/JPEG bytes and caches them
in memory. Missing, corrupt, unsupported, or unrecognized files show abbreviation
badges instead. A fresh checkout works without any of this optional artwork.

`APPROVED_TEAM_LOGOS` in `ui_components.py` is the compatibility name for the
user-selected **local display** mapping. It does not mean that redistribution or
licensing has been approved. Lookup also accepts GS/GSW, NY/NYK, NO/NOP, SA/SAS,
UTAH/UTA, WSH/WAS, and PHO/PHX without changing stored team names or picks.

## Source and rights status

The collection contains no source URLs, license documents, or embedded permission
records. The original download sites cannot be established from these files.
The earlier ESPN URL investigation does not establish that this collection came
from ESPN, nor grant rights to it.

Redistribution permission is unverified. All 30 files and the duplicate
`backend/kings.png` are explicitly ignored by Git; the original SheBETS branding
remains separate. No image is renamed, converted, cropped, or overwritten by the
integration. Local display is not a determination of legal permission.

Before distributing artwork, establish each file's source and applicable
permission for the intended use. The [NBA terms](https://www.nba.com/termsofuse)
and [Disney/ESPN terms](https://disneytermsofuse.com/english/) are reference
points, not licenses attached to these files. Do not force-add ignored artwork
or include logo-bearing screenshots in public materials without reviewing rights.

## Collection notes

All 30 team identities were visually checked across the inventory and integration
reviews. The collection has 26 PNGs and four JPEGs (`bucks.jpg`, `knicks.jpg`,
`magic.jpg`, `spurs.jpg`). All decode correctly. None has transparency.

The Knicks checkerboard is embedded in its JPEG and remains visible. The Spurs,
Bucks, and Magic images have softer edges; several images have extra whitespace.
The smallest images (Celtics, Hawks, Nuggets) are 204 by 192 pixels, sufficient
for the current small badge size. The set mixes symbols and wordmarks; it has
not been certified as a uniform set of current primary marks. Object-fit styling
preserves proportions; a white badge background accommodates opaque artwork.

The Kings file in `backend/` is an identical extra copy and is not used. Tests
generate small, plain synthetic image files in temporary directories, so no
private artwork is necessary to run the suite.
