"""The language of what laterna writes for people to read: config.yaml
`language`, en (the default) or nl. Only the PDF export uses it so far
(laterna/documents.py); the apps on screen stay in English.

Every text has a key and is written out in every language right under
it, so a missing translation shows at once; tr(key, **values) gives it
in the current language, with the values filled in ({name} fields,
str.format). At import every text is checked to have all languages with
the same fields. The names from config.yaml (scale_mm_per_px, look,
start: desk, ...) stay as they are, so they can be found in the file.
"""

import string

LANGUAGES = ('en', 'nl')

_language = 'en'


def use(language):
    """Write in `language` from now on (None: English)."""
    global _language
    if language not in (None, *LANGUAGES):
        raise ValueError(f'language is one of {", ".join(LANGUAGES)}')
    _language = language or 'en'


def tr(key, **values):
    """The text `key` in the current language, `values` filled in."""
    text = TEXTS[key][_language]
    return text.format(**values) if values else text


TEXTS = {
    'run.title': {
        'en': 'Cue sheet',
        'nl': 'Draaiboek',
    },
    'run.footer': {
        'en': '{source} · generated {date}',
        'nl': '{source} · gemaakt {date}',
    },
    'run.keys': {
        'en': 'Keys',
        'nl': 'Toetsen',
    },
    'key.next_does': {
        'en': 'next scene (fades)',
        'nl': 'volgende scène (met fade)',
    },
    'key.back_does': {
        'en': 'previous scene',
        'nl': 'vorige scène',
    },
    'key.twice_does': {
        'en': 'twice quickly during a fade: skips it, so quick presses step straight through',
        'nl': '2x snel tijdens een fade: slaat hem over, dus snel drukken stapt er direct doorheen',
    },
    'key.slide_does': {
        'en': 'inside a slideshow: picture back / on (also , and .)',
        'nl': 'in een slideshow: beeld terug / verder (ook , en .)',
    },
    'key.q_does': {
        'en': 'stop, back to the menu',
        'nl': 'stoppen, terug naar het menu',
    },
    'run.blocks': {
        'en': 'State blocks, top right of the projection',
        'nl': 'Statusblokjes, rechtsboven in de projectie',
    },
    'block.red': {
        'en': 'red',
        'nl': 'rood',
    },
    'block.orange': {
        'en': 'orange',
        'nl': 'oranje',
    },
    'block.green': {
        'en': 'green',
        'nl': 'groen',
    },
    'block.blue': {
        'en': 'blue',
        'nl': 'blauw',
    },
    'block.grey': {
        'en': 'grey',
        'nl': 'grijs',
    },
    'block.red_means': {
        'en': 'a transition runs: one key press does nothing, two cut it short',
        'nl': 'een overgang loopt: één toets doet niets, twee breken hem af',
    },
    'block.orange_means': {
        'en': 'moves on by itself, the blocks count down',
        'nl': 'gaat vanzelf door, de blokjes tellen af',
    },
    'block.green_means': {
        'en': 'waiting for a key',
        'nl': 'wacht op een toets',
    },
    'block.blue_means': {
        'en': 'waiting for DMX (master = 0)',
        'nl': 'wacht op DMX (master = 0)',
    },
    'block.grey_means': {
        'en': 'top left: the progress of a slideshow or video',
        'nl': 'linksboven: de voortgang van een slideshow of video',
    },
    'run.trouble': {
        'en': 'If something goes wrong',
        'nl': 'Als er iets misgaat',
    },
    'run.trouble_restart': {
        'en': 'After Q or a restart the show begins at scene 1. To get back to where you were, press the right arrow quickly a few times: a second press during a fade skips it. The scene number is in the first column of the cue list and in the L label.',
        'nl': 'Na Q of een herstart begint de show bij scène 1. Terug naar waar je was: druk een paar keer snel op het pijltje naar rechts; een tweede druk tijdens een fade slaat hem over. Het scènenummer staat in de eerste kolom van de cuelijst en in het L-label.',
    },
    'run.trouble_backup': {
        'en': 'Last resort: backup.pdf holds every scene full screen in show order. Open it full screen on the projector; no fades, no spots dimming with the desk.',
        'nl': 'Laatste redmiddel: backup.pdf bevat elke scène schermvullend in showvolgorde. Open hem schermvullend op de beamer; geen fades, geen spots die met de tafel dimmen.',
    },
    'run.columns': {
        'en': 'Columns',
        'nl': 'Kolommen',
    },
    'run.columns_note': {
        'en': 'NEXT: how the show goes on to the next scene (AUTO: after how long) and the fade to it. Grey text = the same as the scene before.',
        'nl': 'VERDER: hoe de show naar de volgende scène gaat (AUTO: na hoe lang) en de fade daarheen. Grijze tekst = hetzelfde als de scène ervoor.',
    },
    'col.picture': {
        'en': 'picture',
        'nl': 'beeld',
    },
    'col.objects': {
        'en': 'objects',
        'nl': 'objecten',
    },
    'entry.key': {
        'en': 'KEY',
        'nl': 'TOETS',
    },
    'run.all_black': {
        'en': 'all black',
        'nl': 'alles zwart',
    },
    'run.black': {
        'en': 'black',
        'nl': 'zwart',
    },
    'run.fill': {
        'en': 'fill',
        'nl': 'vulling',
    },
    'run.slideshow_of': {
        'en': 'slideshow of {n}',
        'nl': 'slideshow van {n}',
    },
    'run.stays': {
        'en': '(stays)',
        'nl': '(blijft)',
    },
    'config.title': {
        'en': 'Configuration',
        'nl': 'Configuratie',
    },
    'config.footer': {
        'en': '{source} · generated {date}',
        'nl': '{source} · gemaakt {date}',
    },
    'config.setup': {
        'en': 'Set-up and global calibration',
        'nl': 'Opstelling en globale kalibratie',
    },
    'config.lens_foot': {
        'en': 'lens foot point',
        'nl': 'voetpunt lens',
    },
    'config.picture_width': {
        'en': 'picture {mm} mm = {px} px',
        'nl': 'beeld {mm} mm = {px} px',
    },
    'config.outer_edges': {
        'en': 'outer edges {mm} mm',
        'nl': 'buitenranden {mm} mm',
    },
    'config.setup_note': {
        'en': 'Black = the whole projector picture on the frame plane at the set scale. Red dot = origin of an object (middle of the bottom of its frame). Light numbers between frames = the gap, frame to frame, in mm.',
        'nl': 'Zwart = het hele beamerbeeld op het lijstvlak bij de ingestelde schaal. Rode stip = oorsprong van een object (midden van de onderkant van de lijst). Lichte getallen tussen de lijsten = de tussenruimte, lijst tot lijst, in mm.',
    },
    'config.global': {
        'en': 'Global',
        'nl': 'Globaal',
    },
    'config.picture_on_plane': {
        'en': 'picture on frame plane',
        'nl': 'beeld op lijstvlak',
    },
    'unit.deg': {
        'en': '{v} deg',
        'nl': '{v} graden',
    },
    'config.projector': {
        'en': 'Projector (parallax)',
        'nl': 'Beamer (parallax)',
    },
    'unit.mm_off': {
        'en': '{v} mm (0 = off)',
        'nl': '{v} mm (0 = uit)',
    },
    'config.objects': {
        'en': 'Objects',
        'nl': 'Objecten',
    },
    'col.name': {
        'en': 'name',
        'nl': 'naam',
    },
    'col.wood_mm': {
        'en': 'frame mm',
        'nl': 'lijst mm',
    },
    'config.wood_note': {
        'en': 'Frame = its outer contour (mitred, before the rounding).',
        'nl': 'Lijst = de buitencontour (verstek, vóór de afronding).',
    },
    'config.origin': {
        'en': 'origin',
        'nl': 'oorsprong',
    },
    'config.wood_size': {
        'en': 'frame {v} mm',
        'nl': 'lijst {v} mm',
    },
    'config.canvas_size': {
        'en': 'canvas {v} mm',
        'nl': 'doek {v} mm',
    },
    'legend.wood': {
        'en': 'frame (outer contour, mitred)',
        'nl': 'lijst (buitencontour, verstek)',
    },
    'legend.molding': {
        'en': 'projected molding (rounded)',
        'nl': 'geprojecteerde molding (afgerond)',
    },
    'legend.canvas': {
        'en': 'canvas = picture area',
        'nl': 'doek = beeldvlak',
    },
    'legend.corner': {
        'en': 'inner corner = calibration point',
        'nl': 'binnenhoek = kalibratiepunt',
    },
    'config.placement': {
        'en': 'Placement',
        'nl': 'Plaatsing',
    },
    'config.corners': {
        'en': 'Inner corners (frame.inner, local mm)',
        'nl': 'Binnenhoeken (frame.inner, lokale mm)',
    },
    'col.projector_px': {
        'en': 'projector px',
        'nl': 'beamer-px',
    },
    'config.corners_note': {
        'en': 'Edges of the inner polygon: {lo} to {hi} mm, {n} corners. In step 4 (page corners) < > selects a corner, the arrows move it 1 mm (Shift 10).',
        'nl': 'Zijden van de binnenpolygoon: {lo} tot {hi} mm, {n} hoeken. In stap 4 (pagina corners) kiest < > een hoek, de pijlen verschuiven hem 1 mm (Shift 10).',
    },
    'config.molding': {
        'en': 'Molding and light',
        'nl': 'Molding en licht',
    },
    'config.molding_note': {
        'en': 'Every canvas white, at full light, top of the largest frame: the profile, the fixed light direction and the shadow the molding throws on the canvas.',
        'nl': 'Elk doek wit, bij vol licht, bovenkant van de grootste lijst: het profiel, de vaste lichtrichting en de schaduw die de molding op het doek werpt.',
    },
    'look.molding': {
        'en': 'brightness of the molding',
        'nl': 'helderheid van de molding',
    },
    'look.fill': {
        'en': 'brightness of the fill (no picture)',
        'nl': 'helderheid van de vulling (geen beeld)',
    },
    'look.images': {
        'en': 'brightness of pictures and video',
        'nl': 'helderheid van beelden en video',
    },
    'look.temperature': {
        'en': 'colour of the light (K)',
        'nl': 'kleur van het licht (K)',
    },
    'look.white': {
        'en': "the projector's white (K)",
        'nl': 'het wit van de beamer (K)',
    },
    'look.spot_collapse': {
        'en': 'spot flattens as the light dims (0 = off)',
        'nl': 'spot vlakt af als het licht dimt (0 = uit)',
    },
    'look.frame_depth': {
        'en': 'parallax strip, mm (0 = off)',
        'nl': 'parallaxstrook, mm (0 = uit)',
    },
    'spot.type': {
        'en': 'cone (lamp in front) or gaussian (soft pool)',
        'nl': 'cone (lamp ervoor) of gaussian (zachte vlek)',
    },
    'spot.strength': {
        'en': 'dark edge = 1 - strength',
        'nl': 'donkere rand = 1 - strength',
    },
    'spot.position': {
        'en': 'lamp / centre, fraction of the frame (y 0 = bottom)',
        'nl': 'lamp / midden, deel van de lijst (y 0 = onder)',
    },
    'spot.aim': {
        'en': 'cone: where the axis hits',
        'nl': 'cone: waar de as raakt',
    },
    'spot.distance': {
        'en': 'cone: lamp in front, x frame width',
        'nl': 'cone: lamp ervoor, x lijstbreedte',
    },
    'spot.angle': {
        'en': 'cone: half opening angle',
        'nl': 'cone: halve openingshoek',
    },
    'spot.softness': {
        'en': 'cone: penumbra, x angle',
        'nl': 'cone: halfschaduw, x hoek',
    },
    'spot.falloff': {
        'en': 'cone: distance falloff exponent',
        'nl': 'cone: exponent van de afname met de afstand',
    },
    'spot.size': {
        'en': 'gaussian: sigma, x frame size',
        'nl': 'gaussian: sigma, x lijstgrootte',
    },
    'spot.images': {
        'en': 'x strength on pictures',
        'nl': 'x strength op beelden',
    },
    'spot.fill': {
        'en': 'x strength on the fill',
        'nl': 'x strength op de vulling',
    },
    'config.spot': {
        'en': 'The pretend spot: how the light falls',
        'nl': 'De nagebootste spot: hoe het licht valt',
    },
    'config.with_spot': {
        'en': 'with the spot, as set',
        'nl': 'met de spot, zoals ingesteld',
    },
    'config.spot_map': {
        'en': 'spot strength on the picture (false colour)',
        'nl': 'spotsterkte op het beeld (valse kleuren)',
    },
    'config.without_spot': {
        'en': 'without the spot (strength 0)',
        'nl': 'zonder de spot (strength 0)',
    },
    'config.spot_note': {
        'en': 'Every canvas white, no pictures: only the light. Each frame gets its own spot, placed relative to its own outline, so all frames get the same fan of light.',
        'nl': 'Elk doek wit, geen beelden: alleen het licht. Elke lijst krijgt zijn eigen spot, geplaatst ten opzichte van zijn eigen omtrek, dus alle lijsten krijgen dezelfde lichtwaaier.',
    },
    'config.spot_table': {
        'en': 'spot (picture and fill)',
        'nl': 'spot (beeld en vulling)',
    },
    'config.scene_spot': {
        'en': 'The spot on a scene: {name}',
        'nl': 'De spot op een scène: {name}',
    },
    'config.with_spot_show': {
        'en': 'with the spot, as in the show',
        'nl': 'met de spot, zoals in de show',
    },
    'config.scene_spot_note': {
        'en': "The spot brightens each frame around its aim point and lets the edges fall back to {edge}, so the frames look lit by the theatre light. As the desk dims the light the pool flattens first (spot_collapse {collapse}); at full light nothing changes. The colour comes from look.temperature ({kelvin} K) or the desk's cct channel.",
        'nl': 'De spot maakt elke lijst lichter rond zijn richtpunt en laat de randen terugvallen naar {edge}, zodat de lijsten door het theaterlicht verlicht lijken. Als de tafel het licht dimt, vlakt de vlek eerst af (spot_collapse {collapse}); bij vol licht verandert er niets. De kleur komt van look.temperature ({kelvin} K) of het cct-kanaal van de tafel.',
    },
    'config.dmx_input': {
        'en': 'Input',
        'nl': 'Ingang',
    },
    'config.dmx_channels': {
        'en': 'Channels',
        'nl': 'Kanalen',
    },
    'col.dmx_channel': {
        'en': 'DMX channel',
        'nl': 'DMX-kanaal',
    },
    'col.function': {
        'en': 'function',
        'nl': 'functie',
    },
    'config.dmx_note': {
        'en': 'Channel = address + offset - 1. Objects sharing a channel dim together. cct: 128 = look.temperature, 0-128 from the warm end, 128-255 to the cool end (dmx.cct). start full: a channel is full (cct 128) until the desk changes its value; start desk: the desk rules from its first frame. smooth: time constants up / down in seconds.',
        'nl': 'Kanaal = address + offset - 1. Objecten op één kanaal dimmen samen. cct: 128 = look.temperature, 0-128 vanaf het warme eind, 128-255 naar het koele eind (dmx.cct). start full: een kanaal is vol (cct 128) tot de tafel zijn waarde verandert; start desk: de tafel bepaalt vanaf zijn eerste frame. smooth: tijdconstanten op / neer in seconden.',
    },
    'config.appendix': {
        'en': 'Appendix: config.yaml',
        'nl': 'Bijlage: config.yaml',
    },
    'config.continued': {
        'en': ' (continued)',
        'nl': ' (vervolg)',
    },
    'run.scene': {
        'en': 'scene',
        'nl': 'scène',
    },
    'entry.auto': {
        'en': 'AUTO',
        'nl': 'AUTO',
    },
    'spot.color': {
        'en': 'tint',
        'nl': 'tint',
    },
    'run.video_of': {
        'en': 'video {name}',
        'nl': 'video {name}',
    },
    'backup.title': {
        'en': 'Backup',
        'nl': 'Backup',
    },
    'config.object': {
        'en': 'Object {id} · {name}',
        'nl': 'Object {id} · {name}',
    },
    'key.space': {
        'en': 'Space',
        'nl': 'Spatie',
    },
    'key.l_does': {
        'en': 'scene label on/off (drawn in the projection itself)',
        'nl': 'scènelabel aan/uit (in de projectie zelf getekend)',
    },
    'key.p_does': {
        'en': 'state blocks top right: half, full, off',
        'nl': 'statusblokjes rechtsboven: half, vol, uit',
    },
    'legend.step_key': {
        'en': 'on a key',
        'nl': 'op een toets',
    },
    'legend.step_desk': {
        'en': 'on a key or by the desk',
        'nl': 'op een toets of door de tafel',
    },
    'legend.step_auto': {
        'en': 'by itself',
        'nl': 'vanzelf',
    },
    'legend.blackout': {
        'en': 'blackout',
        'nl': 'blackout',
    },
    'legend.slideshow': {
        'en': 'slideshow',
        'nl': 'slideshow',
    },
    'legend.video': {
        'en': 'video',
        'nl': 'video',
    },
    'scenes.title': {
        'en': 'Scenes',
        'nl': 'Scènes',
    },
    'run.fade': {
        'en': 'fade {t}',
        'nl': 'fade {t}',
    },
    'col.next': {
        'en': 'next',
        'nl': 'verder',
    },
    'run.columns_slides': {
        'en': 'A row of small pictures = the combinations of a slideshow.',
        'nl': 'Een rij kleine beelden = de combinaties van een slideshow.',
    },
}


def _fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


for _key, _text in TEXTS.items():
    if set(_text) != set(LANGUAGES):
        raise ValueError(f'i18n {_key}: needs {", ".join(LANGUAGES)}, has {", ".join(_text)}')
    if len({frozenset(_fields(t)) for t in _text.values()}) > 1:
        raise ValueError(f'i18n {_key}: the languages have different {{fields}}')
