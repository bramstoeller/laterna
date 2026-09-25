"""The backup as slides (backup.pptx), written with zipfile only: no
python-pptx (lxml is a native dependency), just the few parts PowerPoint
and Impress need for picture slides.

The same pictures as the backup PDF, one slide each, full screen on black,
but played like play.py plays them: each scene fades in over its fade,
slideshow pictures fade over their transition_time at their time, and a
scene with a hold moves on by itself when the hold has run out; the other
scenes wait for a click or key (every slide also moves on at a click).
"""

import zipfile
from xml.sax.saxutils import escape, quoteattr

EMU_PER_PX = 6350  # 1920 px -> 12192000 EMU, PowerPoint's 16:9 width

NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
NS_P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
NS_P14 = 'http://schemas.microsoft.com/office/powerpoint/2010/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'application/vnd.openxmlformats-officedocument.presentationml.'
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
XMLNS = f'xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"'

BLACK_BG = (
    '<p:bg><p:bgPr><a:solidFill><a:srgbClr val="000000"/></a:solidFill>'
    '<a:effectLst/></p:bgPr></p:bg>'
)
EMPTY_TREE = (
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
)
CLR_MAP = (
    'bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
    'hlink="hlink" folHlink="folHlink"'
)


def _rels(items):
    """[(id, type, target)] -> a .rels part."""
    rows = ''.join(
        f'<Relationship Id="{i}" Type="{REL}{t}" Target="{target}"/>' for i, t, target in items
    )
    return f'{HEAD}<Relationships xmlns="{PKG_REL}">{rows}</Relationships>'


def _theme():
    colours = ''.join(
        f'<a:{name}><a:srgbClr val="{val}"/></a:{name}>'
        for name, val in (
            ('dk1', '000000'),
            ('lt1', 'FFFFFF'),
            ('dk2', '1F1F1F'),
            ('lt2', 'EEEEEE'),
            ('accent1', '4472C4'),
            ('accent2', 'ED7D31'),
            ('accent3', 'A5A5A5'),
            ('accent4', 'FFC000'),
            ('accent5', '5B9BD5'),
            ('accent6', '70AD47'),
            ('hlink', '0563C1'),
            ('folHlink', '954F72'),
        )
    )
    font = '<a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/>'
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = f'<a:ln w="9525">{fill}</a:ln>'
    effect = '<a:effectStyle><a:effectLst/></a:effectStyle>'
    return (
        f'{HEAD}<a:theme xmlns:a="{NS_A}" name="laterna"><a:themeElements>'
        f'<a:clrScheme name="laterna">{colours}</a:clrScheme>'
        f'<a:fontScheme name="laterna"><a:majorFont>{font}</a:majorFont>'
        f'<a:minorFont>{font}</a:minorFont></a:fontScheme>'
        f'<a:fmtScheme name="laterna"><a:fillStyleLst>{fill * 3}</a:fillStyleLst>'
        f'<a:lnStyleLst>{line * 3}</a:lnStyleLst>'
        f'<a:effectStyleLst>{effect * 3}</a:effectStyleLst>'
        f'<a:bgFillStyleLst>{fill * 3}</a:bgFillStyleLst></a:fmtScheme>'
        '</a:themeElements></a:theme>'
    )


def _transition(fade, advance):
    """The transition into a slide: a fade of `fade` seconds (0 = a cut),
    and after `advance` seconds (None = never) on to the next slide by
    itself. PowerPoint 2010+ reads the fade's exact duration (p14:dur);
    older readers get a slow fade."""
    attrs = '' if advance is None else f' advTm="{round(advance * 1000)}"'
    if fade <= 0:
        return f'<p:transition{attrs}/>' if attrs else ''
    return (
        f'<mc:AlternateContent xmlns:mc="{NS_MC}">'
        f'<mc:Choice xmlns:p14="{NS_P14}" Requires="p14">'
        f'<p:transition spd="slow" p14:dur="{round(fade * 1000)}"{attrs}><p:fade/></p:transition>'
        f'</mc:Choice><mc:Fallback><p:transition spd="slow"{attrs}><p:fade/></p:transition>'
        '</mc:Fallback></mc:AlternateContent>'
    )


def _slide(name, cx, cy, picture, fade, advance):
    shape = ''
    if picture:
        shape = (
            '<p:pic><p:nvPicPr><p:cNvPr id="2" name="picture"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        )
    return (
        f'{HEAD}<p:sld {XMLNS}><p:cSld name={quoteattr(name)}>{BLACK_BG}'
        f'<p:spTree>{EMPTY_TREE}{shape}</p:spTree></p:cSld>'
        f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>{_transition(fade, advance)}</p:sld>'
    )


def timings(scenes, fades, views):
    """Per slide, in show order: (fade in, seconds until it moves on by
    itself or None). A scene's clock starts when its fade-in has finished
    (play.py starts the hold there); view['t'] is when a slideshow picture
    starts to fade in, view['fade'] how long that takes."""
    out = []
    for i, st in enumerate(scenes):
        hold = st.get('hold') if i + 1 < len(scenes) else None
        vs = views[i]
        for k, v in enumerate(vs):
            fade = (fades[i - 1] if i else 0.0) if k == 0 else v.get('fade', 0.0)
            shown = v['t'] + (fade if k else 0.0)  # fully in, on the scene's clock
            if k + 1 < len(vs):
                advance = max(0.0, vs[k + 1]['t'] - shown)
            else:
                advance = None if hold is None else max(0.0, hold - shown)
            out.append((fade, advance))
    return out


def write(path, cfg, scenes, fades, views, title):
    """Write the backup pptx: every view (export.scene_views) a slide."""
    w, h = cfg['canvas']
    cx, cy = w * EMU_PER_PX, h * EMU_PER_PX
    n = len(scenes)
    slides = []  # (name, jpeg bytes or None)
    for i, st in enumerate(scenes):
        for k, v in enumerate(views[i]):
            name = f'{i + 1}/{n} {st.get("name", "?")}'
            if len(views[i]) > 1:
                name += f' [{k + 1}/{len(views[i])}]'
            slides.append((name, v['full']))
    times = timings(scenes, fades, views)

    parts = {}
    types = [
        ('presentation.main+xml', '/ppt/presentation.xml'),
        ('slideMaster+xml', '/ppt/slideMasters/slideMaster1.xml'),
        ('slideLayout+xml', '/ppt/slideLayouts/slideLayout1.xml'),
        ('presProps+xml', '/ppt/presProps.xml'),
    ]
    parts['_rels/.rels'] = (
        f'{HEAD}<Relationships xmlns="{PKG_REL}">'
        f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
        'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>'
    )
    parts['docProps/core.xml'] = (
        f'{HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:title>{escape(title)}</dc:title><dc:creator>laterna projection export</dc:creator>'
        '</cp:coreProperties>'
    )
    parts['ppt/presentation.xml'] = (
        f'{HEAD}<p:presentation {XMLNS}>'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst>'
        + ''.join(f'<p:sldId id="{256 + j}" r:id="rId{10 + j}"/>' for j in range(len(slides)))
        + f'</p:sldIdLst><p:sldSz cx="{cx}" cy="{cy}"/><p:notesSz cx="6858000" cy="9144000"/>'
        '</p:presentation>'
    )
    parts['ppt/_rels/presentation.xml.rels'] = _rels(
        [
            ('rId1', 'slideMaster', 'slideMasters/slideMaster1.xml'),
            ('rId2', 'theme', 'theme/theme1.xml'),
            ('rId3', 'presProps', 'presProps.xml'),
        ]
        + [(f'rId{10 + j}', 'slide', f'slides/slide{j + 1}.xml') for j in range(len(slides))]
    )
    parts['ppt/presProps.xml'] = f'{HEAD}<p:presentationPr {XMLNS}/>'
    parts['ppt/theme/theme1.xml'] = _theme()
    parts['ppt/slideMasters/slideMaster1.xml'] = (
        f'{HEAD}<p:sldMaster {XMLNS}><p:cSld>{BLACK_BG}<p:spTree>{EMPTY_TREE}</p:spTree>'
        f'</p:cSld><p:clrMap {CLR_MAP}/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        '</p:sldMaster>'
    )
    parts['ppt/slideMasters/_rels/slideMaster1.xml.rels'] = _rels(
        [
            ('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'),
            ('rId2', 'theme', '../theme/theme1.xml'),
        ]
    )
    parts['ppt/slideLayouts/slideLayout1.xml'] = (
        f'{HEAD}<p:sldLayout {XMLNS} type="blank"><p:cSld name="Blank">'
        f'<p:spTree>{EMPTY_TREE}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )
    parts['ppt/slideLayouts/_rels/slideLayout1.xml.rels'] = _rels(
        [('rId1', 'slideMaster', '../slideMasters/slideMaster1.xml')]
    )
    media = {}
    for j, ((name, picture), (fade, advance)) in enumerate(zip(slides, times)):
        rels = [('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml')]
        if picture:
            media[f'ppt/media/image{j + 1}.jpg'] = picture
            rels.append(('rId2', 'image', f'../media/image{j + 1}.jpg'))
        parts[f'ppt/slides/slide{j + 1}.xml'] = _slide(name, cx, cy, picture, fade, advance)
        parts[f'ppt/slides/_rels/slide{j + 1}.xml.rels'] = _rels(rels)
        types.append(('slide+xml', f'/ppt/slides/slide{j + 1}.xml'))

    overrides = ''.join(
        f'<Override PartName="{name}" ContentType="{CT}{t}"/>' for t, name in types
    ) + (
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    )
    content_types = (
        f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        f'{overrides}</Types>'
    )
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        for name, text in parts.items():
            z.writestr(name, text)
        for name, data in media.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED)  # JPEG: already compressed
