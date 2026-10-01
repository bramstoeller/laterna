"""The backup as slides (<show>.pptx), written with zipfile only: no
python-pptx (lxml is a native dependency), just the parts PowerPoint and
Impress need.

One slide per scene, made of objects (laterna/layers.py): the pictures
whole and uncropped, the text as text boxes in its font,
flat colours, and over it all one mask with the frames and black. Played
like play.py plays it: each scene fades in over its fade and moves on by
itself when it has a hold (the others wait for a click or key);
slideshow pictures and a text's lines fade in at their time. The slide's
title (hidden: not on the screen, but in the overview) is the scene's
number and name, its description is in the speaker notes. The font is
named, not embedded: install it (it is in the show folder) where the
slides are shown.
"""

import hashlib
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


def _emu(px):
    return round(px * EMU_PER_PX)


def _xfrm(box, flip=False):
    x, y, w, h = box
    attrs = ' flipH="1"' if flip else ''
    return (
        f'<a:xfrm{attrs}><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
        f'<a:ext cx="{max(1, _emu(w))}" cy="{max(1, _emu(h))}"/></a:xfrm>'
    )


def _pic(sid, obj, rid):
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name={quoteattr(obj["name"])}/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch>'
        f'</p:blipFill><p:spPr>{_xfrm(obj["box"], obj["flip"])}'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def _rect(sid, obj):
    """A rectangle in one colour (raw: a fill, a text's background)."""
    colour = '{:02X}{:02X}{:02X}'.format(*obj['color'])
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name={quoteattr(obj["name"])}/><p:cNvSpPr/>'
        f'<p:nvPr/></p:nvSpPr><p:spPr>{_xfrm(obj["box"])}'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill><a:ln><a:noFill/></a:ln>'
        '</p:spPr></p:sp>'
    )


ALIGN = {'left': 'l', 'center': 'ctr', 'right': 'r'}


def _text(sid, obj, lang):
    """A text box with one part of a line: its box from the left edge to
    the advance, the baseline `ascent` under its top (one line at single
    spacing, no insets)."""
    top = obj['baseline'] - obj['ascent']
    box = (obj['x'], top, obj['width'], obj['ascent'] + obj['descent'])
    colour = '{:02X}{:02X}{:02X}'.format(*obj['color'])
    style = (' b="1"' if obj['bold'] else '') + (' i="1"' if obj['italic'] else '')
    size = max(100, round(obj['size'] * EMU_PER_PX / 127))  # px -> 1/100 pt
    run = (
        f'<a:r><a:rPr lang="{lang}" sz="{size}"{style} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{colour}"/></a:solidFill>'
        f'<a:latin typeface={quoteattr(obj["font"])}/><a:cs typeface={quoteattr(obj["font"])}/>'
        f'</a:rPr><a:t>{escape(obj["text"])}</a:t></a:r>'
    )
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name={quoteattr(obj["name"])}/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr>{_xfrm(box)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        '<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t">'
        '<a:noAutofit/></a:bodyPr><a:lstStyle/>'
        f'<a:p><a:pPr algn="{ALIGN[obj["align"]]}"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc>'
        f'</a:pPr>{run}</a:p></p:txBody></p:sp>'
    )


def _title(name, lang, cx):
    """The slide's title: in the overview, never on the screen (hidden,
    and above the slide, for viewers that show hidden shapes anyway)."""
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title" hidden="1"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="{-_emu(100)}"/>'
        f'<a:ext cx="{cx}" cy="{_emu(80)}"/>'
        '</a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>'
        f'<a:p><a:r><a:rPr lang="{lang}" dirty="0"/><a:t>{escape(name)}</a:t></a:r></a:p>'
        '</p:txBody></p:sp>'
    )


def _timing(effects):
    """The slide's animations: [(shape id, delay, duration, is text)], each
    a fade-in that starts by itself (with the previous) `delay` seconds
    after the slide is in."""
    if not effects:
        return ''
    ids = iter(range(5, 10000))
    pars = []
    for sid, delay, duration, _ in effects:
        target = f'<p:tgtEl><p:spTgt spid="{sid}"/></p:tgtEl>'
        fade = ''
        if duration > 0:
            fade = (
                f'<p:animEffect transition="in" filter="fade"><p:cBhvr>'
                f'<p:cTn id="{next(ids)}" dur="{round(duration * 1000)}"/>{target}'
                '</p:cBhvr></p:animEffect>'
            )
        pars.append(
            f'<p:par><p:cTn id="{next(ids)}" presetID="10" presetClass="entr" presetSubtype="0" '
            'fill="hold" grpId="0" nodeType="withEffect">'
            f'<p:stCondLst><p:cond delay="{round(delay * 1000)}"/></p:stCondLst><p:childTnLst>'
            f'<p:set><p:cBhvr><p:cTn id="{next(ids)}" dur="1" fill="hold"><p:stCondLst>'
            f'<p:cond delay="0"/></p:stCondLst></p:cTn>{target}<p:attrNameLst>'
            '<p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
            f'<p:to><p:strVal val="visible"/></p:to></p:set>{fade}</p:childTnLst></p:cTn></p:par>'
        )
    builds = ''.join(
        f'<p:bldP spid="{sid}" grpId="0" animBg="1"/>' for sid, _, _, text in effects if text
    )
    slide = '<p:tgtEl><p:sldTgt/></p:tgtEl>'
    return (
        '<p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" '
        'nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek">'
        '<p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>'
        '<p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/>'
        '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>'
        '<p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst>'
        f'<p:childTnLst>{"".join(pars)}</p:childTnLst></p:cTn></p:par>'
        '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn>'
        f'<p:prevCondLst><p:cond evt="onPrev" delay="0">{slide}</p:cond></p:prevCondLst>'
        f'<p:nextCondLst><p:cond evt="onNext" delay="0">{slide}</p:cond></p:nextCondLst>'
        '</p:seq></p:childTnLst></p:cTn></p:par></p:tnLst>'
        + (f'<p:bldLst>{builds}</p:bldLst>' if builds else '')
        + '</p:timing>'
    )


def _notes(text, lang):
    body = ''.join(
        f'<a:p><a:r><a:rPr lang="{lang}" dirty="0"/><a:t>{escape(line)}</a:t></a:r></a:p>'
        for line in (text or '').splitlines() or ['']
    )
    return (
        f'{HEAD}<p:notes {XMLNS}><p:cSld><p:spTree>{EMPTY_TREE}'
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image"/><p:cNvSpPr><a:spLocks noGrp="1" '
        'noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr>'
        '</p:nvSpPr><p:spPr/></p:sp>'
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>'
        '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
    )


NOTES_SIZE = (6858000, 9144000)


def _notes_master():
    nw, nh = NOTES_SIZE

    def ph(sid, name, kind, idx, y, h):
        idx = f' idx="{idx}"' if idx else ''
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr>'
            f'<a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="{kind}"{idx}/></p:nvPr>'
            f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{nw // 10}" y="{y}"/>'
            f'<a:ext cx="{nw * 8 // 10}" cy="{h}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:sp>'
        )

    return (
        f'{HEAD}<p:notesMaster {XMLNS}><p:cSld><p:spTree>{EMPTY_TREE}'
        + ph(2, 'Slide Image', 'sldImg', None, nh // 12, nh * 4 // 12)
        + ph(3, 'Notes', 'body', 1, nh * 6 // 12, nh * 5 // 12)
        + f'</p:spTree></p:cSld><p:clrMap {CLR_MAP}/></p:notesMaster>'
    )


def _slide(name, lang, cx, cy, shapes, fade, advance):
    """shapes: [(xml, fade or None, is text)] back to front, their shape
    ids from 3 on (2 is the title)."""
    tree = _title(name, lang, cx) + ''.join(xml for xml, _, _ in shapes)
    effects = [(3 + k, f[0], f[1], text) for k, (_, f, text) in enumerate(shapes) if f is not None]
    return (
        f'{HEAD}<p:sld {XMLNS}><p:cSld name={quoteattr(name)}>{BLACK_BG}'
        f'<p:spTree>{EMPTY_TREE}{tree}</p:spTree></p:cSld>'
        f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>{_transition(fade, advance)}'
        f'{_timing(effects)}</p:sld>'
    )


TITLE_STYLES = (
    '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2400"/></a:lvl1pPr></p:titleStyle>'
    '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
    '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
)


def write(path, cfg, scenes, fades, objects, title):
    """Write a pptx: a slide per scene, of its objects (layers.Layers.scene,
    one list per scene)."""
    w, h = cfg['canvas']
    cx, cy = w * EMU_PER_PX, h * EMU_PER_PX
    lang = {'nl': 'nl-NL'}.get(cfg.get('language') or 'en', 'en-GB')
    n = len(scenes)
    title_ph = (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
        '</p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm>'
        f'<a:off x="0" y="0"/><a:ext cx="{cx}" cy="{_emu(80)}"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr/></a:p></p:txBody></p:sp>'
    )

    parts = {}
    types = [
        ('presentation.main+xml', '/ppt/presentation.xml'),
        ('slideMaster+xml', '/ppt/slideMasters/slideMaster1.xml'),
        ('slideLayout+xml', '/ppt/slideLayouts/slideLayout1.xml'),
        ('notesMaster+xml', '/ppt/notesMasters/notesMaster1.xml'),
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
        '<p:notesMasterIdLst><p:notesMasterId r:id="rId4"/></p:notesMasterIdLst>'
        '<p:sldIdLst>'
        + ''.join(f'<p:sldId id="{256 + j}" r:id="rId{10 + j}"/>' for j in range(n))
        + f'</p:sldIdLst><p:sldSz cx="{cx}" cy="{cy}"/>'
        f'<p:notesSz cx="{NOTES_SIZE[0]}" cy="{NOTES_SIZE[1]}"/></p:presentation>'
    )
    parts['ppt/_rels/presentation.xml.rels'] = _rels(
        [
            ('rId1', 'slideMaster', 'slideMasters/slideMaster1.xml'),
            ('rId2', 'theme', 'theme/theme1.xml'),
            ('rId3', 'presProps', 'presProps.xml'),
            ('rId4', 'notesMaster', 'notesMasters/notesMaster1.xml'),
        ]
        + [(f'rId{10 + j}', 'slide', f'slides/slide{j + 1}.xml') for j in range(n)]
    )
    parts['ppt/presProps.xml'] = f'{HEAD}<p:presentationPr {XMLNS}/>'
    parts['ppt/theme/theme1.xml'] = _theme()
    parts['ppt/theme/theme2.xml'] = _theme()
    parts['ppt/slideMasters/slideMaster1.xml'] = (
        f'{HEAD}<p:sldMaster {XMLNS}><p:cSld>{BLACK_BG}<p:spTree>{EMPTY_TREE}{title_ph}'
        f'</p:spTree></p:cSld><p:clrMap {CLR_MAP}/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        f'{TITLE_STYLES}</p:sldMaster>'
    )
    parts['ppt/slideMasters/_rels/slideMaster1.xml.rels'] = _rels(
        [
            ('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'),
            ('rId2', 'theme', '../theme/theme1.xml'),
        ]
    )
    parts['ppt/slideLayouts/slideLayout1.xml'] = (
        f'{HEAD}<p:sldLayout {XMLNS} type="titleOnly"><p:cSld name="Title only">'
        f'<p:spTree>{EMPTY_TREE}{title_ph}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )
    parts['ppt/slideLayouts/_rels/slideLayout1.xml.rels'] = _rels(
        [('rId1', 'slideMaster', '../slideMasters/slideMaster1.xml')]
    )
    parts['ppt/notesMasters/notesMaster1.xml'] = _notes_master()
    parts['ppt/notesMasters/_rels/notesMaster1.xml.rels'] = _rels(
        [('rId1', 'theme', '../theme/theme2.xml')]
    )
    media = {}  # sha1 -> part name: a picture used again is stored once
    for i, scene in enumerate(scenes):
        name = f'{i + 1}/{n} {scene.get("name", "?")}'
        fade = fades[i - 1] if i else 0.0
        hold = scene.get('hold') if i + 1 < n else None
        rels = [
            ('rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'),
            ('rId2', 'notesSlide', f'../notesSlides/notesSlide{i + 1}.xml'),
        ]
        shapes = []
        for k, obj in enumerate(objects[i]):
            sid = 3 + k
            if obj['kind'] == 'text':
                shapes.append((_text(sid, obj, lang), obj['fade'], True))
                continue
            if obj['kind'] == 'rect':
                shapes.append((_rect(sid, obj), obj['fade'], False))
                continue
            digest = hashlib.sha1(obj['data']).hexdigest()
            if digest not in media:
                media[digest] = (f'ppt/media/image{len(media) + 1}.{obj["ext"]}', obj['data'])
            rid = f'rId{len(rels) + 1}'
            rels.append((rid, 'image', '../' + media[digest][0].removeprefix('ppt/')))
            shapes.append((_pic(sid, obj, rid), obj['fade'], False))
        parts[f'ppt/slides/slide{i + 1}.xml'] = _slide(
            name, lang, cx, cy, shapes, fade, None if hold is None else float(hold)
        )
        parts[f'ppt/slides/_rels/slide{i + 1}.xml.rels'] = _rels(rels)
        parts[f'ppt/notesSlides/notesSlide{i + 1}.xml'] = _notes(scene.get('description'), lang)
        parts[f'ppt/notesSlides/_rels/notesSlide{i + 1}.xml.rels'] = _rels(
            [
                ('rId1', 'notesMaster', '../notesMasters/notesMaster1.xml'),
                ('rId2', 'slide', f'../slides/slide{i + 1}.xml'),
            ]
        )
        types.append(('slide+xml', f'/ppt/slides/slide{i + 1}.xml'))
        types.append(('notesSlide+xml', f'/ppt/notesSlides/notesSlide{i + 1}.xml'))

    overrides = (
        ''.join(f'<Override PartName="{name}" ContentType="{CT}{t}"/>' for t, name in types)
        + ''.join(
            f'<Override PartName="/ppt/theme/theme{k}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            for k in (1, 2)
        )
        + (
            '<Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        )
    )
    content_types = (
        f'{HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        f'{overrides}</Types>'
    )
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        for name, text in parts.items():
            z.writestr(name, text)
        for name, data in media.values():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED)  # already compressed
