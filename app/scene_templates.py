"""Scene templates: the repeatable scenes that make a channel look like itself.

The viral cinematic-character reels are not one idea, they are a small set of
scenes played over and over: a boss arrives, a rival answers, an auction
escalates, a cup of chai lands the joke. Each template below carries the
camera language, the prompt skeleton, the dialogue beats and the sound cue for
one of those scenes, so a series can plan its scenes instead of improvising a
generic storyboard every time.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SceneTemplate:
    template_id: str
    label: str
    summary: str
    camera: str
    prompt: str
    beats: tuple[str, ...]
    sound: str
    duration_seconds: int = 5
    credit_cost: int = 23
    location: str = ""


TEMPLATES: tuple[SceneTemplate, ...] = (
    SceneTemplate(
        template_id="boss_entry",
        label="Boss entry",
        summary="The boss arrives and the room changes.",
        camera=(
            "Low-angle slow push-in on the lead character walking towards camera, "
            "shallow depth of field, warm rim light, background figures stepping back"
        ),
        prompt=(
            "{character} walks towards the camera through a {location}, slow confident "
            "stride, coat moving, guards and onlookers stepping aside, "
            "cinematic volumetric haze, golden key light from the side"
        ),
        beats=("silence before the entrance", "the walk", "the room reacts", "one heavy line"),
        sound="Deep low drone that swells, footsteps, a distant crowd hush",
        location="marble palace hall",
    ),
    SceneTemplate(
        template_id="boss_convoy",
        label="Boss convoy",
        summary="A luxury convoy rolls in.",
        camera=(
            "Wide low tracking shot of a convoy moving slowly past camera, then a cut "
            "to a close-up of the lead character inside the car"
        ),
        prompt=(
            "A convoy of black luxury cars rolls slowly through {location}, the lead "
            "character visible through the tinted window, wet asphalt reflections, "
            "night city lights"
        ),
        beats=("the convoy arrives", "the car stops", "the window drops", "one line"),
        sound="Engine growl, tyres on wet road, a slow cinematic beat",
        location="night city street",
    ),
    SceneTemplate(
        template_id="auction",
        label="The auction",
        summary="Two rivals bid a ridiculous amount and the room loses its mind.",
        camera=(
            "Shot-reverse-shot between two seated characters, cut to the auctioneer "
            "at the podium, then a wide reaction shot of the room"
        ),
        prompt=(
            "Two rival characters sit facing each other in a luxury auction hall, "
            "raising paddles, a glass case with an absurd prize on the stage, "
            "crystal chandeliers, polished marble"
        ),
        beats=("the opening bid", "the rival answers", "the numbers escalate", "the room reacts"),
        sound="Chandelier echo, gasps rising with every bid, the gavel",
        location="luxury auction hall",
    ),
    SceneTemplate(
        template_id="gate_entry",
        label="Palace gate entry",
        summary="The gates open for the boss.",
        camera=(
            "Symmetrical wide shot of tall gates opening, the lead character walking "
            "through towards camera, guard silhouettes on both sides"
        ),
        prompt=(
            "Tall palace gates swing open and {character} walks through towards "
            "camera, guards standing to both sides, warm light spilling out of the "
            "courtyard, cinematic haze"
        ),
        beats=("the order is given", "the gates open", "the walk", "the line"),
        sound="Heavy gate creak, echo, a crowd murmur",
        location="palace courtyard",
    ),
    SceneTemplate(
        template_id="private_jet_chai",
        label="Private jet chai",
        summary="A private jet, a cup of chai, and an ordinary heart.",
        camera=(
            "Slow aerial push over the jet, then a tight handheld shot of a cup of "
            "chai in the cabin, then the character looking out of the window"
        ),
        prompt=(
            "Inside a private jet cabin, {character} sits with a small steel glass of "
            "chai, mountains far below through the window, soft cabin light"
        ),
        beats=("the luxury", "the chai", "the contrast", "the line"),
        sound="Quiet cabin hum, the click of the glass, wind",
        location="private jet cabin",
    ),
    SceneTemplate(
        template_id="helipad_arrival",
        label="Helipad arrival",
        summary="The boss lands on the roof.",
        camera=(
            "High drone shot looking down at a helipad, rotor wash kicking up dust, "
            "then a low shot of the character stepping off"
        ),
        prompt=(
            "A helicopter settles on a skyscraper helipad at dusk, {character} steps "
            "out into the rotor wash, city lights below, cinematic teal and gold"
        ),
        beats=("the rotor sound", "the step out", "the city below", "the line"),
        sound="Rotor wash rising, a door closing, city noise far below",
        location="skyscraper helipad",
    ),
    SceneTemplate(
        template_id="golden_throne",
        label="Golden throne",
        summary="A throne room and a very calm verdict.",
        camera=(
            "Slow dolly-in on the character seated on a golden throne, low warm "
            "key light, falling dust in the air"
        ),
        prompt=(
            "{character} sits on an ornate golden throne in a dark hall, warm gold "
            "light from above, dust motes in the air, deep shadows"
        ),
        beats=("the room", "the verdict", "the promise", "the line"),
        sound="A single low string note, room reverb",
        location="dark throne hall",
    ),
    SceneTemplate(
        template_id="rain_walk",
        label="Rain walk",
        summary="Walking in the rain, unbothered.",
        camera=(
            "Slow-motion tracking shot from the front, rain hitting the shoulders, "
            "street lights flaring behind"
        ),
        prompt=(
            "{character} walks slowly through heavy rain on a night street, "
            "water running off a dark coat, warm street lights blooming behind"
        ),
        beats=("the rain", "the walk", "the street", "the line"),
        sound="Heavy rain, a distant thunder roll, muffled bass",
        location="rain-slick night street",
    ),
    SceneTemplate(
        template_id="street_chai",
        label="Cutting chai",
        summary="Enormous wealth, a ten-rupee cup of chai.",
        camera=(
            "Handheld close-up of a steaming glass of chai on a roadside stall, "
            "then a tilt up to the character standing there"
        ),
        prompt=(
            "A roadside chai stall at night, a small glass of steaming tea on the "
            "counter, {character} standing beside it, warm stall light, busy street behind"
        ),
        beats=("the stall", "the contrast", "the ordinary ritual", "the line"),
        sound="Stall chatter, milk boiling, a spoon on glass",
        location="roadside chai stall",
    ),
    SceneTemplate(
        template_id="school_for_my_son",
        label="School for my son",
        summary="The boss solves a problem with money.",
        camera=(
            "Wide shot of a school gate with children, then a medium two-shot as the "
            "character hands something over"
        ),
        prompt=(
            "Outside a school gate in warm afternoon light, {character} handing a "
            "folder to a proud parent, children in the background, hopeful tone"
        ),
        beats=("the problem", "the offer", "the reaction", "the line"),
        sound="Children, a school bell, warm strings",
        location="school gate",
    ),
    SceneTemplate(
        template_id="return_to_village",
        label="Back to the village",
        summary="The boss comes home, unchanged.",
        camera=(
            "Wide establishing shot of a village lane, then a slow push-in as the "
            "convoy stops and the character steps out"
        ),
        prompt=(
            "A narrow village lane at dawn, {character} stepping out of a luxury car "
            "in simple clothes, villagers turning to look, dust in the sunlight"
        ),
        beats=("the return", "the faces", "the greeting", "the line"),
        sound="Birds, a quiet crowd, warm flute",
        location="village lane",
    ),
    SceneTemplate(
        template_id="police_station_silence",
        label="Police station silence",
        summary="The whole station goes silent when the boss walks in.",
        camera=(
            "Static wide shot of a police station as the character enters, everyone "
            "stops, then a slow push to the character"
        ),
        prompt=(
            "A night police station with officers at their desks, {character} "
            "entering and every officer turning to look, fluorescent light, tense quiet"
        ),
        beats=("the door opens", "the silence", "the reaction", "the line"),
        sound="A door creak, a clock ticking, everything else stops",
        location="night police station",
    ),
    SceneTemplate(
        template_id="baraat_royal_entry",
        label="Royal baraat entry",
        summary="The wedding procession stops for the boss.",
        camera=(
            "Tracking shot through the crowd, then the procession parting, then a "
            "close-up of the character in festive light"
        ),
        prompt=(
            "A festive Indian wedding procession in a narrow street, {character} "
            "arriving in the middle of it, marigold and string lights, people cheering"
        ),
        beats=("the procession", "the parting", "the greeting", "the line"),
        sound="Dhol, cheering, a shehnai swell",
        location="wedding street",
    ),
)

TEMPLATES_BY_ID: dict[str, SceneTemplate] = {item.template_id: item for item in TEMPLATES}


def template_for(template_id: str) -> SceneTemplate | None:
    return TEMPLATES_BY_ID.get(template_id)


def templates_for_pack(pack_id: str) -> tuple[SceneTemplate, ...]:
    """The scene vocabulary a pack may use."""
    if pack_id == "desi_boss_monologue":
        return tuple(
            item
            for item in TEMPLATES
            if item.template_id
            in {
                "street_chai",
                "rain_walk",
                "golden_throne",
                "return_to_village",
                "private_jet_chai",
            }
        )
    return TEMPLATES


def plan_scene_templates(topic: str, pack_id: str, part_count: int) -> list[SceneTemplate]:
    """Assign a scene template to every part of a series.

    The choice is deterministic so a series keeps its visual identity, and it
    rotates so six parts do not look like the same scene six times.
    """
    vocabulary = templates_for_pack(pack_id)
    if not vocabulary:
        return []
    digest = sum(ord(char) for char in topic) if topic else 0
    plan: list[SceneTemplate] = []
    for index in range(part_count):
        position = (digest + index * 3) % len(vocabulary)
        plan.append(vocabulary[position])
    return plan


def template_directive(template: SceneTemplate) -> str:
    """The scene direction injected into the writer prompt."""
    return (
        f"Scene: {template.label} — {template.summary} "
        f"Location: {template.location}. "
        f"Camera: {template.camera}. "
        f"Beats: {'; '.join(template.beats)}. "
        f"Sound: {template.sound}. "
        "Write the words for this scene only: short spoken lines, no narration "
        "about the scene."
    )
