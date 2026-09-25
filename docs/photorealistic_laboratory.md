# Fotorealistisches, artikuliertes Labor

Stand vom 23.09.2026 für den lokalen Branch `cramera-port`.

## Als allgemeine CRAM-Welt verwenden

`LaboratoryEnvironment` lädt das Labor in eine normale
`semantic_digital_twin.world.World`. Das mitgelieferte Assetpaket enthält die
Laborbank, die bewegliche Schublade, den Ständer, drei unabhängig bewegliche
Reagenzgläser und den Stopfen. Es wird mit dem Python-Paket installiert;
`~/.cramera`, eine PR2-Aufzeichnung, Blender und ein laufender Browser oder
Server sind zum Laden nicht erforderlich. Die Python-Umgebung muss die normalen
CRAM-/Semantic-Digital-Twin-Abhängigkeiten enthalten.

```python
from cramera.laboratory_world import (
    LaboratoryBody,
    LaboratoryEnvironment,
    LaboratorySlot,
)

laboratory = LaboratoryEnvironment()
world = laboratory.create_world()
tube = world.get_body_by_name(LaboratoryBody.CLEAR_TUBE)
destination = laboratory.slot_pose(LaboratorySlot.A3, world=world)
```

`destination` beschreibt den Glasboden in Metern im Bezugssystem `world.root`.
Die sechs Steckplätze A1–A3 und B1–B3 sowie die ursprüngliche Belegung stehen in
`laboratory.description.slots` und `laboratory.description.occupancy`. Die
Belegungsmetadaten beschreiben die Ausgangslage; sie werden nicht automatisch
nach jedem Plan aktualisiert. Körperposen liest man aus der erzeugten Welt.

Roboter werden über die vorhandene `RobotSpecification` hinzugefügt. Derselbe
Einstieg akzeptiert andere Roboter oder mehrere Spezifikationen mit eindeutigen
`prefix`-Werten. Beispielsweise entsteht so ein nativer CRAM-Kontext für den PR2:

```python
import math

from coraplex.datastructures.dataclasses import Context
from semantic_digital_twin.api import RobotSpecification
from semantic_digital_twin.robots.pr2 import PR2
from semantic_digital_twin.spatial_types import HomogeneousTransformationMatrix

world = laboratory.create_world(
    robots=[
        RobotSpecification(
            semantic_annotation_type=PR2,
            odom_T_robot_start=HomogeneousTransformationMatrix.from_xyz_rpy(
                x=0.40, y=-0.72, yaw=math.pi / 2
            ),
        )
    ]
)
[robot] = world.get_semantic_annotations_by_type(PR2)
context = Context(world=world, robot=robot)
tube = world.get_body_by_name(LaboratoryBody.CLEAR_TUBE)
destination = laboratory.slot_pose(LaboratorySlot.A3, world=world)
```

Die Roboterbeschreibungen müssen wie bei anderen CRAM-Welten installiert sein.
Der Loader wählt weder einen Arm noch einen Greifer oder eine Greifstrategie.
Die passende Reichweite, Gelenkstellung und Greifplanung hängen vom Roboter ab.
Für den bereits validierten PR2-Transfer bleibt
`python -m cramera.laboratory_demo` verfügbar.

Eine bereits bestehende Welt lässt sich mit `laboratory.populate(world)` ergänzen.
Der Import verwendet die unveränderten Labornamen und Ursprungslage; diese Namen
dürfen in der Zielwelt noch nicht vorhanden sein. Eine abweichende selbst erstellte
Version lässt sich mit `LaboratoryEnvironment(bundle_directory=Path(...))` laden.

Standardmäßig ergänzt der allgemeine Loader keine Ausnahmen für
Glas–Ständer-Kollisionen. Für Pläne mit beabsichtigtem Auflage- und Einsetzkontakt
kann man gezielt `LaboratoryEnvironment(allow_support_contacts=True)` verwenden.
Diese Option erlaubt ausschließlich die Kontakte der drei Gläser zum Ständer;
sie ist keine allgemeine Kollisionsabschaltung.

Ein vollständiges, headless ausführbares Beispiel liegt unter
[`cramera.examples.laboratory_world`](../src/cramera/examples/laboratory_world.py).
Es lädt die Welt, bindet bei gewähltem Roboter einen CRAM-Kontext und gibt Objekte
und Steckplätze aus. Vom Repository-Verzeichnis aus:

```bash
.venv/bin/python -m cramera.examples.laboratory_world
.venv/bin/python -m cramera.examples.laboratory_world --robot pr2
.venv/bin/python -m cramera.examples.laboratory_world --robot hsrb
```

Dies ist eine wiederverwendbare Planungswelt mit Gelenken, Objektposen und
Kollisionsgeometrie. Das Laden startet keine Bewegung und keine MuJoCo-Simulation.
Der weiter unten beschriebene PR2-Kontaktmodus ist weiterhin eine separate
Ausführung gespeicherter Gelenkziele; beliebige neue CRAM-Pläne erhalten durch
den Loader noch kein allgemeines MuJoCo-Backend.

## Flüssigkeit in der Kontaktansicht

Die Ansichten `precision_lab_physics` und `precision_lab_pr2_physics` ergänzen die
MuJoCo-Kontakte um ein vereinfachtes Flüssigkeitsmodell. Die Oberfläche richtet
sich an der Schwerkraft aus und schwappt bei Beschleunigung. Beim Kippen über den
Rand sinkt die Füllmenge; austretende Tropfen folgen ballistischen Bahnen.
Flüssigkeit, die durch die Öffnung eines anderen Glases fällt, erhöht dessen
Füllmenge. Andere Tropfen werden als ausgelaufene Menge geführt und auf der
Arbeitsplatte als Pfützen dargestellt. Das Backend bilanziert Inhalt, Tropfen und
ausgelaufene Menge gemeinsam.

Die Darstellung verbindet zeitlich und räumlich zusammenhängende
Flüssigkeitsportionen zu einem glatten Strahl. Frischer Ausfluss beginnt am
gemessenen tiefsten Punkt des Glasrands; unterbrochene Strahlen und abgelöste
Tropfen bleiben getrennt. Die sichtbare Strahlgeometrie erhält das Volumen der
zugrunde liegenden Portionen.

Bedienung im rechten Bereich **Flüssigkeit**:

1. Ein Glas auswählen; im PR2-Modus gegebenenfalls den Roboter pausieren.
2. Die gewünschte Menge in Millilitern eingeben und **Füllung setzen** drücken.
3. **Aus dem Ständer heben** und warten, bis das Glas tatsächlich angehoben ist.
   **Glas ansehen** richtet die Kamera auf die aktuelle Glasposition aus.
4. Mit den Kippknöpfen neigen; **Aufrichten** bringt die Zielorientierung zurück.
5. **Labor zurücksetzen** stellt auch die ursprünglichen Füllungen wieder her.

Die Kippsteuerung setzt ein begrenztes Drehmomentziel. Sie überschreibt keine
Objektpose; der Ständer und andere Körper können die gewünschte Drehung blockieren.
Die Flüssigkeit folgt der gemessenen Glasbewegung, nicht der Zielpose. Die
Anzeige nennt die aktuelle Füllmenge, ausgelaufene Menge und tatsächliche Neigung.
Ein neuer PR2-Durchlauf stellt die Ausgangspositionen wieder her und übernimmt die
zuletzt gewählten Füllmengen. Der Transferknopf bewegt das gefüllte Glas nach A3.

### Zwei Flüssigkeiten mit dem PR2 mischen

In `precision_lab_pr2_physics` startet **Misch-Demo starten** einen festen Ablauf:

1. Die Ausgangspositionen werden wiederhergestellt; die beiden Quellgläser
   erhalten jeweils 5 ml, die klare Phiole startet leer.
2. Der PR2 gießt das bernsteinfarbene Quellglas in die klare Phiole in A1
   und stellt das leere Quellglas nach A3.
3. Er stellt die teilweise gefüllte Phiole nach A2 um. Dadurch wird die
   vordere Anfahrt zum türkisfarbenen Quellglas in B1 frei.
4. Er gießt die zweite Flüssigkeit in die Phiole in A2 und stellt das
   leere Quellglas zurück nach B1.
5. Er nimmt die Phiole auf, bewegt ihren Boden dreimal auf einer Kreisbahn
   um den Griffpunkt und stellt sie wieder in A2 ab.

**PR2 pausieren** hält die Gelenkziele; die Kontaktphysik läuft weiter.
**Misch-Demo fortsetzen** setzt einen pausierten Ablauf mit den aktuellen
Füllmengen fort. Ein neuer Durchlauf startet die feste Rezeptur wieder von vorn.
Manuelles Bewegen und Befüllen sind während der Roboterbewegung gesperrt.

Die Mischdemo verwendet einen Frontgriff: Der Greifer fährt horizontal an die
Glaswand heran, schließt seine Finger langsam und hebt erst bei beidseitigem
Kontakt an. Die Öffnung berücksichtigt die benachbarten Gläser im Ständer.
Das hintere Quellglas wird mit einer um 25 Grad versetzten Frontanfahrt
aufgenommen. Beim Ausgießen bleibt die Öffnung des gehaltenen Glases frei;
das Gießziel folgt der tatsächlich gemessenen Randmitte der Empfängerphiole.
Während des eigentlichen Gießens liegt die Ausgusskante zwei Zentimeter über
dem Empfängerrand. Die Anfahrt richtet das Glas zuerst mit größerem Abstand aus,
damit die Finger beim Absenken an der Empfängerphiole vorbeipassen.
Vor dem Abstellen richtet der Roboter den Griff wieder nach der ursprünglichen
Anfahrt des jeweiligen Steckplatzes aus. Eine auf einen halben Millimeter
begrenzte Absetzvorgabe übergibt die Last an den Ständer. Erst nach gemessener,
stabiler Bodenunterstützung öffnet er die Finger bei ruhender Hand und zieht
anschließend den offenen Greifer zurück.

Für das kreisende Schwenken teilen sich die Demo und die neue Coraplex-Aktion
`SwirlingAction` die Geometrie `SwirlTrajectory`: Der Griffpunkt bleibt fest,
die Neigung wird sanft aufgebaut und wieder auf null zurückgeführt. Die Demo
prüft die tatsächlich gemessenen Umdrehungen bei beidseitigem Fingerkontakt.
Die [Coraplex-Aktion](../../coraplex/examples/swirl_container.md) verwendet
Giskard-Ziele für Position, Orientierung, begrenzte Neigung und Geschwindigkeit.
Die Browserdemo verfolgt dieselbe Bahn mit ihrem lokalen MuJoCo-Kontaktregler;
sie führt keinen vollständigen Giskard-Plan aus.

Die Demo steuert physische Gelenkantriebe und beobachtet die erreichten
Glasposen. Die Flüssigkeitsmengen verändern sich durch den berechneten Ausfluss
und das Auffangen in der Phiole. Farben werden beim Auffangen entsprechend den
Volumenanteilen gemischt; das anschließende Schwenken zeigt die Bewegung der
freien Oberfläche. Räumliche Durchmischung und chemische Reaktionen werden
nicht berechnet. Dies ist ein fester lokaler Roboterablauf, kein allgemeiner
CRAM-Planer für beliebige Ausgießaufgaben.

Der PR2-Kontaktmodus verwendet drei zusätzliche MuJoCo-NoSlip-Iterationen,
damit die gehaltenen Gläser unter Schwerkraft nicht durch die numerische
Kontaktregularisierung langsam zwischen den Fingern verdrehen. Reibungswerte
und Kraftgrenzen bleiben erhalten. Die Einstellung ergänzt die normalen
Kontaktkräfte; sie verbindet Glas und Greifer nicht durch eine feste Bindung.
Siehe [MuJoCo: Slow slippage](https://mujoco.readthedocs.io/en/stable/modeling.html#slow-slippage).

Das Modell ist eine Näherung aus einer gedämpften freien Oberfläche,
volumenerhaltendem Ausfluss und ballistischen Flüssigkeitsportionen. Es löst keine
Navier–Stokes-Gleichungen und ist keine SPH-Simulation. Druckkräfte und
Schwappmomente werden nicht berechnet. Die enthaltene Flüssigkeit
erhöht inzwischen die Körpermasse und die angenäherte Trägheit des Glases in
MuJoCo; dadurch verändern sich Gewicht und Kontaktkräfte auch am Greifer.
Benetzung, Kapillarwirkung, chemische Reaktionen und detaillierte Kollisionen der
Tropfen mit Rack, Roboter oder Einrichtungsgegenständen sind nicht modelliert.
Die sichtbaren Pfützen und das Auffangen in Glasöffnungen dienen dem interaktiven
Prototyp; Messwerte sind keine kalibrierte Laborsimulation.

MuJoCos eigene Fluid-Optionen beschreiben Kräfte eines umgebenden Mediums auf
starre Körper. Die freie Flüssigkeitsoberfläche wird deshalb separat berechnet:
[MuJoCo: Fluid forces](https://mujoco.readthedocs.io/en/stable/computation/fluid.html).

## Funktionale Waage in der Kontaktansicht

In beiden Kontaktansichten zeigt die Waage die gemessene Last ihrer Schale in
Gramm an, sowohl am Instrument als auch im Bereich **Waage**. MuJoCo liefert die
Kontaktkräfte an der Schale; die Anzeige rechnet deren vertikale Komponente mit
der simulierten Schwerkraft in eine Masse um. Ein darüber gehaltenes Glas zählt
erst, wenn es tatsächlich Last auf die Schale überträgt. Zusätzlicher Druck oder
eine noch aktive Haltekraft können den Messwert beeinflussen.

Ein offener Haltering auf der Schale stützt die runden Glasböden seitlich, damit
die Gläser nach dem Loslassen aufrecht stehen bleiben. Seine sichtbaren
24 Segmente entsprechen den Kollisionsflächen. Kräfte über den Ring gehen
ebenfalls in die Anzeige ein; Kontakte am Gerätegehäuse zählen nicht mit.
Der fest montierte Halter gehört zum unbelasteten Nullpunkt der Waage.

1. Ein Objekt auswählen und einen laufenden PR2-Ablauf pausieren.
2. **Objekt auf die Waage** hebt es an, bewegt es über die Schale und setzt es ab.
   Die Bewegung verwendet die Kontaktsteuerung. Bei blockiertem Weg bleibt das
   Objekt gehalten; **Transport anhalten** unterbricht die Bewegung.
3. Auf **Stabil** warten. **Waage ansehen** richtet die Kamera auf das Instrument.
4. **Tara / Nullstellen** übernimmt die ruhende Last als Nullpunkt. So lässt sich
   zuerst ein leeres Glas tarieren und anschließend seine Füllung wiegen.

Ein leeres Reagenzglas hat die modellierte Masse von 12 g. Alle Flüssigkeiten
verwenden derzeit die angenommene Dichte von 1 g/ml: 5 ml erhöhen die ruhende Last
um 5 g. Beim Ausgießen und Auffangen wird diese zusätzliche Körpermasse mit der
aktuellen Füllmenge aktualisiert. Die Trägheitsverteilung bleibt eine Näherung;
die Flüssigkeit wird für diese Lastberechnung am Trägheitsrahmen des Glases
zusammengefasst. Freifliegende Tropfen und Pfützen belasten die Waage nicht.

Die Anzeige glättet Kontaktfluktuationen und zeigt drei Nachkommastellen. **Stabil**
setzt mindestens eine halbe Sekunde mit ausreichend ruhiger Last voraus.
Tarieren ist bei unruhiger Last oder laufendem Roboterablauf gesperrt.
**Labor zurücksetzen** löscht auch die Tara. Nach Entfernen eines tarierten
Gefäßes ist eine negative Anzeige korrekt. Die Nachkommastellen beschreiben die
Anzeigeauflösung, keine kalibrierte Messgenauigkeit.

Die Zustandsantworten enthalten `scale` mit `grams`, `grossGrams`, `tareGrams`,
`stable`, `forceNewtons`, `objects` und `available`. Die Befehle
`POST /api/laboratory/physics/scale/tare` und
`POST /api/laboratory/physics/robot/scale/tare` akzeptieren ausschließlich `{}`.
Die geschützte Serveransicht verwendet denselben Robot-Befehl unter ihrem
`/laboratory`-Präfix und benötigt weiterhin die Anmeldung.

## Umgesetzter erster Prototyp

Die Szene `precision_lab` enthält eine gestaltete Laborbank mit Regal und
Laborzubehör, eine ausziehbare Schublade, einen Ständer mit sechs offenen
Steckplätzen, drei unabhängige Hohlgläser und einen abnehmbaren Stopfen.
Zwei Gläser enthalten getrennte sichtbare Flüssigkeitskörper.

Die lokale Vorschau ist unter
[Laborbank in CRAMERA](http://127.0.0.1:8716/index.html?scene=precision_lab&layout=scene&offline=1)
erreichbar, solange der Vorschauprozess läuft. Rechts befindet sich die Bedienung
für Schublade, Glasaufnahme, Einsetzen, Stopfen, Kameras und Zurücksetzen.
Gläser werden über diese Bedienung bewegt; freies Ziehen ist hier abgeschaltet,
damit die angezeigte Belegung der Steckplätze konsistent bleibt.

Die Blender-Datei und erzeugten Assets liegen lokal unter
`~/.cramera/scenes/precision_lab/`:

- `laboratory.blend`: editierbare Modelle, Materialien und zwei Kameras.
- `render_overview.png`, `render_closeup.png`: Cycles-Referenzbilder.
- `environment.urdf`: getrennte sichtbare Modelle und Schubladengelenk.
- `tube_*.urdf`, `stopper.urdf`: einzelne Körper mit Kollisionsbeschreibungen.
- `scene.json`, `trajectory.json`: Szene und ein einzelner Initialzustand.
- `semantics.json`: Maße, Greifhöhe, Steckplätze und Validierungsgrenzen.
- `assets/`: GLB-Modelle, Glasboden-Kollision und gemessene Modellabmessungen.

Die manuellen Schaltflächen setzen Objektpositionen kinematisch. Sie sind keine aufgezeichnete
Roboterbewegung und verändert keine laufende CRAM-Welt. Steckplatzbelegung und
Stopfenbindung gelten für die aktuelle Browsersitzung. Ein Neuladen setzt sie
zurück. Der unten beschriebene PR2-Auftrag startet eine eigene Simulationswelt.
Diese ursprüngliche Ansicht berechnet keine Kontaktkräfte oder Flüssigkeitsdynamik.
Die Kollisionsabdeckung des dekorativen Raums und Schrankinneren ist unvollständig.
Der unten beschriebene Kontaktmodus verwendet einzelne Wandsegmente und einen
konvex behandelten Glasboden als Näherung für die dynamische Kollision.

Die in diesem Checkout enthaltene Three.js-Version r128 stellt Transmission über
Alpha-Blending dar. Ein gezielter Kompatibilitätsmodus erhält die Glasmaterialien;
physikalische Brechung und das Blender-Bild sind damit im Browser nicht gleichwertig.
Die Laboransicht verwendet deshalb keine opake SSAO-Nachbearbeitung.

Geprüft: 80 gezielte Pytest-Fälle einschließlich der über Pytest gestarteten
JavaScript-Prüfungen. Die Browserprüfung hat Schubladenöffnung, Aufnahme,
Stopfenbindung und Einsetzen des klaren Glases in A3 bestätigt. Die anderen
Gläser bleiben unabhängig stehen. Alle referenzierten lokalen Assets sind
vorhanden; die GLB-Dateien und Blender-Datei wurden gelesen und die erzeugten
Referenzbilder visuell geprüft.

## PR2: Glas A1 nach A3

Die Schaltfläche **PR2: A1 → A3** startet einen lokalen, simulierten CRAM-Plan.
Die [separate PR2-Steuerung](http://127.0.0.1:8716/laboratory-pr2.html)
funktioniert auch ohne WebGL. Der Auftrag lädt die gespeicherte Laboraufstellung;
manuelle Änderungen in einem Browser-Tab werden nicht als Auftrag übernommen.

`LaboratoryWorld` importiert die ursprünglichen URDFs, unabhängige Gläser und
Schublade sowie den PR2. Zusätzliche Kollisionsflächen decken die festen
Schrankpaneele ab. `LaboratoryDemo` verwendet native `PickUpAction` und
`PlaceAction` mit Giskard-Kollisionsvermeidung. Der Greifer nähert sich von oben,
diagonal zu den beiden Ständerreihen, greift 105 mm oberhalb des Glasbodens und
hebt das Glas um 160 mm. Die Standposition ist `(0.40, -0.72, π/2)`, der Torso
steht an seiner geladenen Obergrenze von 0.325 m.

Die freie Anfahrt oberhalb des Ziels hat 3 mm Positionstoleranz. Einsetzen und
Rückzug behalten die engere Toleranz von 0.5 mm und 0.003 rad. Für Greifer und
getragenes Glas gelten im Labor 3 mm Vermeidungsabstand. Nur beabsichtigte
Glas–Greifer- und Glas–Ständer-Kontakte sind ausgenommen. Die Messung der übrigen
aktiven Kollisionspaare läuft bei jedem Bewegungsschritt.

Erfolg erfordert abgeschlossene native Aktionen, beobachtetes Tragen und Anheben,
Freigabe in A3, höchstens 1 mm endgültige Positionsabweichung, unveränderte
Nachbargläser und Stopfen sowie keine gemessene Durchdringung größer als 0.1 mm
in den geprüften Paaren. Der Server veröffentlicht Erfolg erst nach Prozessende
und einem frischen erfolgreichen Messbericht. Parallele Starts sind gesperrt.

Die aufgezeichnete Szene heißt `precision_lab_pr2`; unter
`~/.cramera/scenes/precision_lab_pr2/` liegen `scene.json`, `trajectory.json` und
`acceptance_metrics.json`. Die Aufzeichnung entsteht aus den tatsächlichen
Simulationszuständen. Die ursprünglichen Labor-GLBs einschließlich Glas- und
Flüssigkeitsmaterialien bleiben erhalten. Live-Ansicht und Aufzeichnung enthalten
keine manuelle Glassteuerung.

Die PR2-Demo liest Kamera, Belichtung, Umgebungsverschattung und Materialerhaltung
direkt aus der ursprünglichen `precision_lab/scene.json`. Dieselben Einstellungen
gelten für den Live-Export und die gespeicherte Aufzeichnung. Auch asynchron
nachgeladene Reagenzgläser behalten ihre Glasbehandlung und leuchtenden
Materialanteile. Der Export übernimmt ausschließlich die Darstellungsvorgaben;
die manuellen Laborsteuerungen werden nicht in die Roboteransicht übernommen.
Eine Änderung dieser Vorgaben invalidiert die zwischengespeicherte Live-Szene.

Die Korrektur ist durch 123 gezielte Python-Tests und 19 über Pytest gestartete
JavaScript-Prüfungen abgedeckt. Ein weiterer über die PR2-Schaltfläche gestarteter
Lauf bestätigte die ursprünglichen Darstellungsvorgaben sowohl im ausgelieferten
Live-Bundle und Objektkatalog als auch in der fertigen Aufzeichnung. Der native
Transfer blieb erfolgreich. Der visuelle Bildvergleich ist im eingebetteten
Browser weiterhin durch dessen fehlenden WebGL-Kontext blockiert.

Der über die Browser-Schaltfläche gestartete Abnahmelauf vom 22.09.2026 enthält
1.456 aufgezeichnete Zustände und 666 Bewegungsschritte mit angehängtem Glas.
Pickup und Place wurden erfolgreich beendet. Gemessen wurden 164.2 mm maximale
Anhebung, 0.0153 mm endgültige Positionsabweichung, 0.000113 rad Winkelfehler und
3.898 mm kleinster Abstand in den geprüften Kollisionspaaren. Alle referenzierten
Assets sind vorhanden. Der native Ausführungs-/Welttestlauf bestand mit 20 Tests;
die anschließende Auswahl aus Server-, Viewer-, Geometrie- und Akzeptanztests
bestand mit 49 Tests. Diese Gruppen überlappen und sind nicht zu addieren.

Dies validiert die kinematische Roboterbewegung in den vorhandenen
Kollisionsmodellen. Reibung, Greifkräfte, Glasbruch, Flüssigkeit und reale Hardware
sind nicht Bestandteil dieser Ausführung. Griffe, Füße und dekorative Raumobjekte
haben weiterhin keine vollständige Kollisionsabdeckung.

### Begrenzter Greiferschluss für das Glas

Der PR2 schließt beim Laborauftrag bis zu einer geometrisch berechneten
Fingerstellung. Diese wird nach dem Erreichen des Glases aus beiden
Fingerspitzen und der tatsächlichen Glasposition bestimmt. Suchbewegungen laufen
in einer isolierten Weltkopie und gelangen nicht in die Liveansicht oder Aufnahme.
Die native Schließbewegung verwendet eine Winkelgeschwindigkeit von höchstens
0.1 rad/s und eine Zielwinkeltoleranz von 0.0001 rad. Ein nicht zwischen beiden
Fingern liegendes Glas wird als ungültiger Griff abgewiesen.

Der aktualisierte vollständige Lauf enthält 1.608 aufgezeichnete Zustände.
Beide Aktionen waren erfolgreich. Die direkte Prüfung der Finger-Glas-Paare
ergab während des Tragens mindestens 0.246 mm Abstand im Kollisionsmodell;
beim ersten Haltezustand lag der größere Fingerabstand bei 0.259 mm.
Die gemessene Öffnung zwischen den projizierten Fingerspitzen-Meshes betrug
20.775 mm. Sie enthält die Sicherheitsabstände des Kollisionsmodells und ist
keine Messung einer realen Kontaktkraft. Der Ablauf prüft diese Geometrie
zusätzlich zu den übrigen Kollisionspaaren. Eine Kraftregelung oder
Bruchsimulation ist damit noch nicht verbunden.

```bash
.venv/bin/python -m cramera.laboratory_demo
.venv/bin/python -m pytest test/cramera_test/test_laboratory_demo.py test/cramera_test/test_laboratory_world.py test/cramera_test/test_laboratory_acceptance.py -q -o faulthandler_timeout=0
```

Der vollständige Ausführungstest benötigt das erzeugte Labor-Assetpaket.
Die Geometrie-, Status-, Server- und Akzeptanztests verwenden darüber hinaus
lokal erzeugte Testkörper und benötigen keine Blender-Ausführung.

## Kontaktphysik beim Bewegen der Gläser

Die Schaltfläche **Labor mit Kontaktphysik** in der manuellen Laboransicht und
auf der PR2-Steuerseite startet eine eigene MuJoCo-Simulation. Ihre Ansicht liegt
unter [Labor mit Kontaktphysik](http://127.0.0.1:8716/index.html?scene=precision_lab_physics&layout=scene&offline=1).
Die ursprünglichen Materialien, Belichtung und Kameras bleiben erhalten. Der
Kontaktmodus enthält die Laborbank, drei Gläser und den Stopfen. Der PR2-Auftrag
verwendet weiterhin seine separate kinematische Welt.

Ein Glas kann direkt seitlich gezogen werden. Beim Loslassen endet die Haltekraft.
Alternativ wählt man rechts das Objekt und **Glas halten**; die Pfeile sowie
**Anheben** und **Absenken** verschieben das Ziel in Schritten von einem Zentimeter.
**Glas loslassen** gibt es frei, **Labor zurücksetzen** stellt die Ausgangslage her.
Nach einem Serverneustart lässt sich die Simulation auf derselben Seite starten.

Die Mausbewegung setzt ein federndes Ziel mit begrenzter Kraft. Die sichtbare
Objektposition stammt ausschließlich aus der Simulation. Schwerkraft, Reibung und
Kontakte können die Bewegung verhindern oder das Glas kippen lassen. Orange
markiert das Ziel, rote Punkte markieren aktive Kontakte. Die Anzeige nennt die
Summe der normalen Kontaktkräfte am ausgewählten Objekt und den Abstand zum Ziel.
Der maximale Cursorzug beträgt 0.6 N; das ist eine Bedienungseinstellung und kein
kalibrierter Grenzwert für Glasbruch.

Die Serverprüfung vom 22.09.2026 bestätigt seitlichen Widerstand am Ständer,
Herausheben und Fallen nach Freigabe. Bei einem seitlich um 30 mm versetzten Ziel
blieb das Glas am Rand hängen und neigte sich; der Abstand zum Ziel betrug etwa
37 mm, der größte Cursorzug 0.452 N. Alle 19 Dateien des ursprünglichen
Laborpakets blieben unverändert. Zehn Tests mit echter MuJoCo-Dynamik prüfen
zusätzlich Auflage, Wiedereinsetzen, Kraftbegrenzung und Zurücksetzen. Die
Serversteuerung und Browserlogik besitzen separate Regressionstests.

Die physikalischen Steckplätze sind derzeit als quadratische Öffnungen angenähert,
die Schublade bleibt geschlossen. Numerische Kontakte erlauben geringe
Durchdringung; im geprüften Randkontakt waren es maximal 0.013 mm. Glasbruch ist
nicht enthalten. Das oben beschriebene Flüssigkeitsmodell ergänzt inzwischen beide
Kontaktansichten. Die folgende PR2-Integration enthält Antriebe und Greifkontakte.
Nach dem Rechnerneustart konnte die Laboransicht am 22.09.2026 auch im
eingebetteten Browser wieder visuell geprüft werden.

## PR2 in derselben Kontaktwelt

**PR2 mit Kontaktphysik** öffnet die gemeinsame Ansicht unter
[PR2 im Kontaktlabor](http://127.0.0.1:8716/index.html?scene=precision_lab_pr2_physics&layout=scene&offline=1).
Der Einstieg befindet sich in der manuellen Physikansicht, der Laborbank und auf
der PR2-Steuerseite. **PR2: A1 → A3 starten** beginnt einen neuen Ablauf aus der
Labor-Ausgangslage; dabei werden Roboter und Objekte zurückgesetzt. **PR2 pausieren**
hält die Zielbewegung an; **PR2 fortsetzen** setzt einen pausierten Ablauf ohne
Zurücksetzen fort. Schwerkraft und Kontakte bleiben auch beim Pausieren
aktiv. **Labor zurücksetzen** stellt Roboter und Objekte wieder an den Anfang.
Es gibt keinen automatischen Start der Bewegung beim Öffnen der Seite.

Die Gelenkziele stammen aus dem zuvor nativ mit CRAM ausgeführten Laborauftrag
`precision_lab_pr2`. Begrenzte MuJoCo-Antriebe führen diese Ziele aus. Der Browser
zeigt die berechneten Gelenkstellungen und freien Objektposen. Das Glas wird durch
Greifkontakt und Reibung getragen; seine aufgezeichneten Positionen werden nicht
auf die Physikwelt übertragen. Vor dem Anheben muss der Controller an beiden
Fingerspitzen Kontakt messen. Ein beendeter Bewegungsverlauf zählt erst als Erfolg,
wenn auch Anheben, Freigabe und Platzierung am Ziel physikalisch gemessen wurden.
Die Oberfläche zeigt Phase, Greifkontaktkraft und maximalen Hub.

Der vollständige, über die Browser-Schaltfläche gestartete Lauf vom 22.09.2026
bestätigte beidseitigen Greifkontakt, rund 161 mm Hub und Freigabe in A3. Nach der
Ergänzung des Zurücksetzens vor einem neuen Durchlauf bestätigte auch der finale
Browserlauf die direkte Simulation: etwa 0.4 mm Zielabweichung und 1.156 N
maximale Summe der Fingerspitzen-Normalkräfte. Messdaten des Browserlaufs liegen im erzeugten
Paket unter `contact_metrics.json`.

Vor dem Absenken korrigiert der Controller die tatsächliche seitliche Glasposition
und Neigung durch begrenzte Änderungen der Armziele. Die Glaspose bleibt ein
Ergebnis der Kontaktberechnung. Ein vollständiger Kontaktmitschnitt bestätigte,
dass weder PR2 noch getragenes Glas die Nachbargläser oder den Stopfen berührten.
Die runden Glasböden erlauben dennoch ein langsames Neigen unter Schwerkraft:
auch bei stillstehendem PR2 bewegten sich benachbarte Glaswurzeln über 60 Sekunden
um etwa 3 bis 4 mm. Nach mehreren Minuten können die Gläser stärker geneigt sein;
ein neuer Durchlauf beginnt deshalb ausdrücklich wieder an der Ausgangslage.
Unveränderte Nachbarpositionen sind kein passendes Kriterium für diesen dynamischen
Modus.

Geprüft wurden die Programmtests einschließlich des vollständigen physikalischen
Transfers und eines Griffs ohne Kontakt, sechs Roboterphysiktests, zehn bestehende
Laborphysiktests sowie die Server- und Oberflächentests. Der Browser bestätigte
Darstellung, Start, Pause, Zurücksetzen und den erfolgreichen vollständigen Ablauf.

Diese erste Anbindung führt gespeicherte CRAM-Gelenkziele mit physikalischer
Rückmeldung aus. Sie ist noch kein allgemeines Backend für neue, während der
Ausführung geplante CRAM-Aufträge. Die mobile Basis bleibt fest, Kopf und Räder
bleiben in ihrer Ausgangsstellung. Roboter-Selbstkollision ist in diesem Prototyp
ausgenommen; die importierten Roboter-Kollisionsflächen interagieren mit dem Labor
und seinen Objekten. Die Servoeinstellungen und Kontaktmaterialien sind
Simulationsparameter, keine kalibrierten Eigenschaften eines realen PR2 oder
Bruchgrenzen der Gläser.

### Reproduzieren

Vom Repository-Verzeichnis aus:

```bash
blender --background --python cramera/tools/laboratory/build_blender.py -- --output /home/hassouna/.cramera/scenes/precision_lab --samples 160 --width 1600
.venv/bin/python cramera/tools/laboratory/bundle.py
CRAMERA_SCENE=precision_lab .venv/bin/python -m cramera.server 8716 --no-browser
```

Blender erstellt zuerst die sichtbaren Modelle; der Bundle-Schritt schreibt die
passenden Beschreibungen. Die Bedienlogik liegt in
`cramera/src/cramera/web/core/laboratory-workbench.js`. Die nachfolgende Planung
beschreibt den weiteren Ausbau.

## Ausgangspunkt

- `cram_viz/scenes/tracy_lab/scene.json` enthält Tracy mit zwei beweglichen Armen,
  eine Medienflasche und einen Sterilitätsbehälter sowie eine aufgezeichnete Aktion.
- `coraplex/demos/coraplex_chemical_laboratory_demo/chemical_laboratory.urdf`
  bindet den Laborraum als ein visuelles OBJ-Mesh ein. Die Umgebung hat ausschließlich
  feste Gelenke und Kollisionskörper für ausgewählte Arbeitsflächen.
- `generate_laboratory_items.py` im selben Demo-Verzeichnis erzeugt Flasche,
  Kolben und einen Ständer mit vier Gläsern als STL. Die vier Gläser sind Teil
  desselben Meshes wie der Ständer. Die Gefäße haben noch keinen ausgearbeiteten
  Innenraum mit Glaswand und separater Flüssigkeit.
- Die Labor-Demo arbeitet laut Implementierung kinematisch und verwendet
  Attach/Detach für das Mitführen von Objekten. Belastbare Kontaktphysik ist
  damit noch nicht nachgewiesen.
- CRAMERA verwendet URDF-Modelle und Three.js. Der lokale Viewer enthält bereits
  einen GLTFLoader mit Transmission-Unterstützung. Der vollständige Importpfad
  für neue Laborassets muss separat geprüft werden.
- `tameMat` und `themeEnvironment` in
  `cramera/src/cramera/web/panels/robot_scene/panel.js` verändern importierte
  Materialien. Unter anderem werden Rauheitswerte begrenzt und einzelne
  Umgebungsoberflächen anhand ihrer Linknamen umgestaltet.
- Lokal verfügbar: Blender 4.0.2.

## Werkzeugentscheidung

Blender dient als Quelle für Geometrie, UVs, Materialien, Beleuchtung und
Referenzrenderings mit Cycles. CRAMERA übernimmt die interaktive Darstellung
und die Verbindung zum semantischen Weltmodell und zu Roboteraktionen.

Für den Browser sollen materialtragende glTF/GLB-Assets geprüft werden.
URDF beziehungsweise das Weltmodell beschreibt Gelenke, Achsen und Grenzen.
Ein GLB-Export allein liefert keine vollständige physikalische Artikulation.
Blender-Materialien und das Browserbild müssen getrennt visuell überprüft werden.

Falls dynamische Greif- und Kontaktphysik benötigt wird, folgt die Einrichtung
im gewählten Simulator. Eine zusätzliche USD-/Isaac-Sim-Anbindung ist eine
Option, keine Voraussetzung für den ersten CRAMERA-Prototyp.

## Erster durchgängiger Arbeitsablauf

Eine Laborbank mit einem Ständer, drei einzeln greifbaren Reagenzgläsern,
einem abnehmbaren Stopfen und einer ausziehbaren Schublade.

1. Schublade öffnen.
2. Ein Reagenzglas am vorgesehenen Greifbereich aufnehmen.
3. Das Glas zu einem freien Platz im Ständer bewegen und einsetzen.
4. Das Glas loslassen; seine Zuordnung zum belegten Platz aktualisieren.
5. Den Stopfen separat aufnehmen und aufsetzen.

Der Roboter wird nach Prüfung von Reichweite und Greiferöffnung ausgewählt.
Tracy ist aufgrund der vorhandenen Laborszene ein Kandidat; die vorhandene
HSR-Demo bietet bereits einen Transportablauf.

## Modellierung der Objekte

| Objekt | Visuelle Ausarbeitung | Bewegung und Semantik |
| --- | --- | --- |
| Laborbank | Abgerundete Kanten, Arbeitsplatte, Lack und Metall | Feste Basis, benannte Arbeits- und Ablageflächen |
| Schublade | Separater Korpus, Front und Griff | Prismatisches Gelenk mit Achse, Anschlägen und Greifpunkt |
| Reagenzglas | Hohle Geometrie, Wandstärke, gerundeter Boden, Glasmaterial | Einzelner starrer Körper, Öffnung, Greifbereich und Einsetztiefe |
| Stopfen | Separates Kunststoff- oder Gummiteil | Abnehmbares Objekt mit definierten Zuständen und Verbindung zum Glas |
| Ständer | Tatsächlich offene Steckplätze | Benannte Plätze, Einsetzachsen und Belegungszustände |
| Flüssigkeit | Bewegliche freie Oberfläche, Tropfen und Pfützen in der Kontaktansicht | Volumenbilanz, Schwappen und vereinfachtes Ausgießen/Auffangen |

Alle Objekte werden in Metern und mit konsistenten Koordinaten modelliert.
Visuelle und Kollisionsgeometrie werden getrennt aufgebaut. Bei Öffnungen und
Steckplätzen dürfen vereinfachte Kollisionskörper den benötigten freien Raum
nicht verschließen. Massen und Trägheiten werden für dynamische Simulation
anhand der gewählten Maße und Materialien festgelegt.

Ein freies Reagenzglas braucht kein Gelenk. Artikulation betrifft beispielsweise
Schubladen, Schranktüren und Scharnierdeckel. Ein abnehmbarer Stopfen benötigt
einen Zustands- und Verbindungswechsel. Ein Schraubverschluss wäre ein späterer,
eigener Mechanismus mit gekoppelter Rotation und Translation.

## Abnahme des ersten Prototyps

- Ein Cycles-Nahbild zeigt nachvollziehbare Glaswand, Öffnung und Füllstand.
- Dieselben Teile laden in CRAMERA ohne fehlende Geometrie oder Texturen.
- Ein Referenzvergleich prüft, ob der Viewer die vorgesehenen Materialien erhält.
- Die Schublade bewegt sich nur auf ihrer Achse innerhalb ihrer Grenzen.
- Jedes Glas lässt sich einzeln bewegen; Ständer und übrige Gläser bleiben stehen.
- Ein Glas passt geometrisch in einen freien Steckplatz; dessen Kollisionsmodell
  lässt die Einsetzbewegung zu.
- Greifen, Einsetzen und Loslassen stimmen zwischen Anzeige und Weltmodell überein.
- Kinematische Ausführung und dynamische Physik werden als unterschiedliche
  Validierungsstände ausgewiesen.

## Ausbau zum vollständigen Labor

Nach dem ersten Arbeitsablauf folgen weitere Laborbänke, Schränke, Spüle,
Abzug mit beweglicher Scheibe, Kühlschrank und Geräte mit bedienbaren Deckeln.
Die Objekte verwenden dieselben Regeln für Materialien, Benennung, Gelenke,
Kollisionen und semantische Zustände.

Auf dem Flüssigkeitsprototyp können Pipettieren und robotergeplantes Umfüllen
aufbauen. Für kalibrierte Fluiddynamik und die Rückwirkung auf den Greifer ist
eine weitergehende gekoppelte Simulation erforderlich.

## Quellen zur Werkzeugentscheidung

- [Blender Cycles](https://docs.blender.org/manual/en/5.0/render/cycles/introduction.html)
- [Blender USD-Export](https://docs.blender.org/manual/en/5.0/files/import_export/usd.html)
- [Isaac Sim: Asset-Struktur und Physik](https://docs.isaacsim.omniverse.nvidia.com/latest/openusd_tuning_tutorials/tutorial_01_asset_structure.html)

Die verlinkte Blender-Dokumentation beschreibt Version 5.0. Die konkrete
Exportkonfiguration ist gegen die lokal eingesetzte Version zu prüfen.
