"""
Vakantie BV — Ontology-Driven Agent Backend
============================================
Dit script vervangt de in-memory JS state uit de demo door een echte
RDFLib triplestore. De agent genereert SPARQL queries i.p.v. hardcoded
tools aan te roepen.

Architectuur:
  HTML frontend  →  FastAPI server  →  Claude (genereert SPARQL)
                                    →  RDFLib triplestore
                                    →  (optioneel) SHACL validatie

Installeer:
  pip install rdflib anthropic fastapi uvicorn

Start server:
  python vakantie_rdf_backend.py

Of gebruik standalone CLI:
  python vakantie_rdf_backend.py --cli
"""

import sys
import json
import uuid
import re
import anthropic
from datetime import date, datetime
from dotenv import load_dotenv
from rdflib import Graph, ConjunctiveGraph, Namespace, Literal, URIRef, BNode
from rdflib.namespace import RDF, RDFS, OWL, XSD, FOAF
from rdflib.plugins.sparql import prepareQuery
from pyshacl import validate as shacl_validate

load_dotenv()

# ═══════════════════════════════════════════════════════════════
#  NAMESPACES
# ═══════════════════════════════════════════════════════════════
VAKANTIE = Namespace("https://vakantie.nl/ontology#")
DATA     = Namespace("https://vakantie.nl/data#")
SCHEMA   = Namespace("https://schema.org/")

# ═══════════════════════════════════════════════════════════════
#  OWL ONTOLOGIE  (Turtle)
#  Dit is de "Language" laag — equivalent aan Palantir's OMS
# ═══════════════════════════════════════════════════════════════
ONTOLOGY_TTL = """
@prefix vakantie: <https://vakantie.nl/ontology#> .
@prefix owl:      <http://www.w3.org/2002/07/owl#> .
@prefix rdfs:     <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:      <http://www.w3.org/2001/XMLSchema#> .
@prefix foaf:     <http://xmlns.com/foaf/0.1/> .
@prefix schema:   <https://schema.org/> .

# ── Klassen ──────────────────────────────────────────────────
vakantie:Klant a owl:Class ;
    rdfs:label "Klant"@nl ;
    rdfs:subClassOf foaf:Person ;
    vakantie:readOnly true ;
    rdfs:comment "Een reizende klant van Vakantie BV — beheerd door administratie"@nl .

vakantie:Hotel a owl:Class ;
    rdfs:label "Hotel"@nl ;
    rdfs:subClassOf schema:LodgingBusiness ;
    vakantie:readOnly true ;
    rdfs:comment "Een accommodatie op een bestemming — beheerd door administratie"@nl .

vakantie:Bestemming a owl:Class ;
    rdfs:label "Bestemming"@nl ;
    rdfs:subClassOf schema:Place ;
    vakantie:readOnly true ;
    rdfs:comment "Een reisbestemming op de wereld — beheerd door administratie"@nl .

vakantie:Boeking a owl:Class ;
    rdfs:label "Boeking"@nl ;
    rdfs:subClassOf schema:Reservation ;
    rdfs:comment "Verbindt een Klant met een Hotel voor een periode"@nl .

# ── Datatype Properties ───────────────────────────────────────
vakantie:klantId a owl:DatatypeProperty ;
    rdfs:domain vakantie:Klant ; rdfs:range xsd:integer .

vakantie:naam a owl:DatatypeProperty ;
    rdfs:range xsd:string .

vakantie:email a owl:DatatypeProperty ;
    rdfs:domain vakantie:Klant ; rdfs:range xsd:string .

vakantie:loyaltyPunten a owl:DatatypeProperty ;
    rdfs:domain vakantie:Klant ; rdfs:range xsd:integer .

vakantie:sterren a owl:DatatypeProperty ;
    rdfs:domain vakantie:Hotel ; rdfs:range xsd:integer .

vakantie:prijsPerNacht a owl:DatatypeProperty ;
    rdfs:domain vakantie:Hotel ; rdfs:range xsd:decimal .

vakantie:beschikbareKamers a owl:DatatypeProperty ;
    rdfs:domain vakantie:Hotel ; rdfs:range xsd:integer .

vakantie:land a owl:DatatypeProperty ;
    rdfs:domain vakantie:Bestemming ; rdfs:range xsd:string .

vakantie:klimaat a owl:DatatypeProperty ;
    rdfs:domain vakantie:Bestemming ; rdfs:range xsd:string .

vakantie:checkIn a owl:DatatypeProperty ;
    rdfs:domain vakantie:Boeking ; rdfs:range xsd:date .

vakantie:checkOut a owl:DatatypeProperty ;
    rdfs:domain vakantie:Boeking ; rdfs:range xsd:date .

vakantie:aantalPersonen a owl:DatatypeProperty ;
    rdfs:domain vakantie:Boeking ; rdfs:range xsd:integer .

vakantie:status a owl:DatatypeProperty ;
    rdfs:domain vakantie:Boeking ; rdfs:range xsd:string .

vakantie:totaalprijs a owl:DatatypeProperty ;
    rdfs:domain vakantie:Boeking ; rdfs:range xsd:decimal .

# ── Object Properties (relaties) ─────────────────────────────
vakantie:heeftBoeking a owl:ObjectProperty ;
    rdfs:label "heeft boeking"@nl ;
    rdfs:domain vakantie:Klant ;
    rdfs:range  vakantie:Boeking .

vakantie:isGeboektIn a owl:ObjectProperty ;
    rdfs:label "is geboekt in"@nl ;
    rdfs:domain vakantie:Boeking ;
    rdfs:range  vakantie:Hotel .

vakantie:isGevestigdIn a owl:ObjectProperty ;
    rdfs:label "is gevestigd in"@nl ;
    rdfs:domain vakantie:Hotel ;
    rdfs:range  vakantie:Bestemming .

# ── Action Types (Palantir-stijl) ─────────────────────────────
# In een volledige implementatie zouden deze Action Types de
# basis zijn voor auto-gegenereerde SPARQL UPDATE templates
# en SHACL validatieregels.

vakantie:MaakBoeking a vakantie:ActionType ;
    rdfs:label "Maak Boeking"@nl ;
    vakantie:createsType vakantie:Boeking ;
    vakantie:requiresInput vakantie:Klant ;
    vakantie:requiresInput vakantie:Hotel ;
    vakantie:allowedRole "klant" ;
    vakantie:allowedRole "admin" ;
    vakantie:precondition "beschikbareKamers > 0" ;
    vakantie:sideEffect "beschikbareKamers - 1" ;
    vakantie:sideEffect "loyaltyPunten + (totaalprijs / 100 * 10)" .

vakantie:AnnuleerBoeking a vakantie:ActionType ;
    rdfs:label "Annuleer Boeking"@nl ;
    vakantie:modifiesType vakantie:Boeking ;
    vakantie:setsProperty vakantie:status ;
    vakantie:allowedRole "klant" ;
    vakantie:allowedRole "admin" ;
    vakantie:sideEffect "beschikbareKamers + 1" .

vakantie:UpdateLoyalty a vakantie:ActionType ;
    rdfs:label "Update Loyalty Punten"@nl ;
    vakantie:modifiesType vakantie:Klant ;
    vakantie:setsProperty vakantie:loyaltyPunten ;
    vakantie:allowedRole "klant" ;
    vakantie:allowedRole "admin" .

# ── Admin-only Action Types ──────────────────────────────────
vakantie:MaakKlant a vakantie:ActionType ;
    rdfs:label "Maak Klant"@nl ;
    vakantie:createsType vakantie:Klant ;
    vakantie:allowedRole "admin" ;
    rdfs:comment "Alleen admin mag nieuwe klanten aanmaken"@nl .

vakantie:MaakHotel a vakantie:ActionType ;
    rdfs:label "Maak Hotel"@nl ;
    vakantie:createsType vakantie:Hotel ;
    vakantie:requiresInput vakantie:Bestemming ;
    vakantie:allowedRole "admin" ;
    rdfs:comment "Alleen admin mag nieuwe hotels aanmaken"@nl .

vakantie:MaakBestemming a vakantie:ActionType ;
    rdfs:label "Maak Bestemming"@nl ;
    vakantie:createsType vakantie:Bestemming ;
    vakantie:allowedRole "admin" ;
    rdfs:comment "Alleen admin mag nieuwe bestemmingen aanmaken"@nl .
"""

# ═══════════════════════════════════════════════════════════════
#  SHACL SHAPES  (validatie-constraints)
#  W3C standaard voor graph-validatie — afdwinging van de
#  ontologie-regels die de Action Types beschrijven.
# ═══════════════════════════════════════════════════════════════
SHACL_TTL = """
@prefix sh:       <http://www.w3.org/ns/shacl#> .
@prefix vakantie: <https://vakantie.nl/ontology#> .
@prefix xsd:      <http://www.w3.org/2001/XMLSchema#> .

# ── Boeking: verplichte velden en relaties ────────────────────
vakantie:BoekingShape a sh:NodeShape ;
    sh:targetClass vakantie:Boeking ;
    sh:property [
        sh:path vakantie:isGeboektIn ;
        sh:class vakantie:Hotel ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Boeking moet gekoppeld zijn aan een bestaand Hotel"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:checkIn ;
        sh:minCount 1 ;
        sh:message "Boeking moet een check-in datum hebben"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:checkOut ;
        sh:minCount 1 ;
        sh:message "Boeking moet een check-out datum hebben"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:status ;
        sh:in ("bevestigd" "geannuleerd" "in_behandeling") ;
        sh:minCount 1 ;
        sh:message "Status moet bevestigd, geannuleerd of in_behandeling zijn"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:totaalprijs ;
        sh:minExclusive 0 ;
        sh:minCount 1 ;
        sh:message "Boeking moet een totaalprijs > 0 hebben"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:aantalPersonen ;
        sh:minInclusive 1 ;
        sh:minCount 1 ;
        sh:message "Boeking moet minimaal 1 persoon hebben"@nl ;
    ] .

# ── Hotel: verplichte velden ──────────────────────────────────
vakantie:HotelShape a sh:NodeShape ;
    sh:targetClass vakantie:Hotel ;
    sh:property [
        sh:path vakantie:isGevestigdIn ;
        sh:class vakantie:Bestemming ;
        sh:minCount 1 ;
        sh:message "Hotel moet gekoppeld zijn aan een bestaande Bestemming"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:beschikbareKamers ;
        sh:minInclusive 0 ;
        sh:minCount 1 ;
        sh:message "Hotel moet een geldig aantal beschikbare kamers hebben (>= 0)"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:prijsPerNacht ;
        sh:minExclusive 0 ;
        sh:minCount 1 ;
        sh:message "Hotel moet een prijs per nacht > 0 hebben"@nl ;
    ] .

# ── Klant: verplichte velden ─────────────────────────────────
vakantie:KlantShape a sh:NodeShape ;
    sh:targetClass vakantie:Klant ;
    sh:property [
        sh:path vakantie:naam ;
        sh:minCount 1 ;
        sh:message "Klant moet een naam hebben"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:email ;
        sh:minCount 1 ;
        sh:message "Klant moet een email hebben"@nl ;
    ] .
"""

# ═══════════════════════════════════════════════════════════════
#  INITIËLE DATA  (RDF Turtle)
#  In productie: geladen vanuit SQL DB via R2RML mapping
# ═══════════════════════════════════════════════════════════════
DATA_TTL = """
@prefix data:     <https://vakantie.nl/data#> .
@prefix vakantie: <https://vakantie.nl/ontology#> .
@prefix xsd:      <http://www.w3.org/2001/XMLSchema#> .

# ── Klanten ───────────────────────────────────────────────────
data:klant1 a vakantie:Klant ;
    vakantie:klantId 1 ;
    vakantie:naam "Anna de Vries" ;
    vakantie:email "anna@example.nl" ;
    vakantie:loyaltyPunten 1250 .

data:klant2 a vakantie:Klant ;
    vakantie:klantId 2 ;
    vakantie:naam "Pieter Janssen" ;
    vakantie:email "pieter@example.nl" ;
    vakantie:loyaltyPunten 340 .

data:klant3 a vakantie:Klant ;
    vakantie:klantId 3 ;
    vakantie:naam "Sofia Martins" ;
    vakantie:email "sofia@example.nl" ;
    vakantie:loyaltyPunten 2870 .

data:klant4 a vakantie:Klant ;
    vakantie:klantId 4 ;
    vakantie:naam "Lars van den Berg" ;
    vakantie:email "lars@example.nl" ;
    vakantie:loyaltyPunten 0 .

# ── Bestemmingen ──────────────────────────────────────────────
data:barcelona a vakantie:Bestemming ;
    vakantie:naam "Barcelona" ;
    vakantie:land "Spanje" ;
    vakantie:klimaat "Mediterraan" .

data:bali a vakantie:Bestemming ;
    vakantie:naam "Bali" ;
    vakantie:land "Indonesië" ;
    vakantie:klimaat "Tropisch" .

data:marrakech a vakantie:Bestemming ;
    vakantie:naam "Marrakech" ;
    vakantie:land "Marokko" ;
    vakantie:klimaat "Droog" .

data:newYork a vakantie:Bestemming ;
    vakantie:naam "New York" ;
    vakantie:land "USA" ;
    vakantie:klimaat "Gematigd" .

# ── Hotels ────────────────────────────────────────────────────
data:hotel1 a vakantie:Hotel ;
    vakantie:naam "Hotel Arts" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 320 ;
    vakantie:beschikbareKamers 12 ;
    vakantie:isGevestigdIn data:barcelona .

data:hotel2 a vakantie:Hotel ;
    vakantie:naam "Catalonia Square" ;
    vakantie:sterren 4 ;
    vakantie:prijsPerNacht 145 ;
    vakantie:beschikbareKamers 8 ;
    vakantie:isGevestigdIn data:barcelona .

data:hotel3 a vakantie:Hotel ;
    vakantie:naam "COMO Uma Ubud" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 280 ;
    vakantie:beschikbareKamers 6 ;
    vakantie:isGevestigdIn data:bali .

data:hotel4 a vakantie:Hotel ;
    vakantie:naam "Komaneka at Bisma" ;
    vakantie:sterren 4 ;
    vakantie:prijsPerNacht 195 ;
    vakantie:beschikbareKamers 15 ;
    vakantie:isGevestigdIn data:bali .

data:hotel5 a vakantie:Hotel ;
    vakantie:naam "La Mamounia" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 410 ;
    vakantie:beschikbareKamers 3 ;
    vakantie:isGevestigdIn data:marrakech .

data:hotel6 a vakantie:Hotel ;
    vakantie:naam "The Plaza" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 895 ;
    vakantie:beschikbareKamers 20 ;
    vakantie:isGevestigdIn data:newYork .

# ── Boekingen ─────────────────────────────────────────────────
data:boeking1 a vakantie:Boeking ;
    vakantie:checkIn "2025-07-15"^^xsd:date ;
    vakantie:checkOut "2025-07-22"^^xsd:date ;
    vakantie:aantalPersonen 2 ;
    vakantie:status "bevestigd" ;
    vakantie:totaalprijs 2240 ;
    vakantie:isGeboektIn data:hotel1 .

data:boeking2 a vakantie:Boeking ;
    vakantie:checkIn "2025-08-01"^^xsd:date ;
    vakantie:checkOut "2025-08-10"^^xsd:date ;
    vakantie:aantalPersonen 2 ;
    vakantie:status "bevestigd" ;
    vakantie:totaalprijs 2520 ;
    vakantie:isGeboektIn data:hotel3 .

data:boeking3 a vakantie:Boeking ;
    vakantie:checkIn "2025-06-20"^^xsd:date ;
    vakantie:checkOut "2025-06-27"^^xsd:date ;
    vakantie:aantalPersonen 1 ;
    vakantie:status "geannuleerd" ;
    vakantie:totaalprijs 1015 ;
    vakantie:isGeboektIn data:hotel2 .

data:boeking4 a vakantie:Boeking ;
    vakantie:checkIn "2025-12-26"^^xsd:date ;
    vakantie:checkOut "2026-01-02"^^xsd:date ;
    vakantie:aantalPersonen 2 ;
    vakantie:status "in_behandeling" ;
    vakantie:totaalprijs 6265 ;
    vakantie:isGeboektIn data:hotel6 .

# heeftBoeking relaties (Klant → Boeking)
data:klant1 vakantie:heeftBoeking data:boeking1 .
data:klant3 vakantie:heeftBoeking data:boeking2 .
data:klant2 vakantie:heeftBoeking data:boeking3 .
data:klant3 vakantie:heeftBoeking data:boeking4 .
"""

# ═══════════════════════════════════════════════════════════════
#  TRIPLESTORE
#  Equivalent aan Palantir's Object Storage V2
# ═══════════════════════════════════════════════════════════════
class VakantieTriplestore:
    """
    In-memory RDFLib triplestore met SPARQL SELECT en UPDATE support.
    
    In productie: vervang door Apache Jena Fuseki, Oxigraph, 
    of Stardog — zelfde SPARQL interface, persistente opslag.
    """
    
    def __init__(self):
        self.graph = Graph()
        self.graph.bind("vakantie", VAKANTIE)
        self.graph.bind("data", DATA)
        self.graph.bind("schema", SCHEMA)
        self.graph.bind("foaf", FOAF)

        # Laad ontologie + data
        self.graph.parse(data=ONTOLOGY_TTL, format="turtle")
        self.graph.parse(data=DATA_TTL, format="turtle")

        # Laad SHACL shapes als aparte graph
        self.shacl_graph = Graph()
        self.shacl_graph.parse(data=SHACL_TTL, format="turtle")
        print(f"✓ Triplestore geladen: {len(self.graph)} triples, {len(self.shacl_graph)} SHACL triples")
    
    def query(self, sparql: str) -> list[dict]:
        """Voer SPARQL SELECT query uit, geeft lijst van dicts."""
        try:
            results = self.graph.query(sparql, initNs={
                "vakantie": VAKANTIE,
                "data": DATA,
                "xsd": XSD,
                "schema": SCHEMA,
            })
            rows = []
            for row in results:
                r = {}
                for var in results.vars:
                    val = row[var]
                    if val is not None:
                        if isinstance(val, Literal):
                            r[str(var)] = val.toPython()
                        elif isinstance(val, URIRef):
                            # Verkorte URI voor leesbaarheid
                            r[str(var)] = str(val).replace(str(DATA), "data:").replace(str(VAKANTIE), "vakantie:")
                        else:
                            r[str(var)] = str(val)
                rows.append(r)
            return {"success": True, "count": len(rows), "results": rows}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def update(self, sparql: str) -> dict:
        """Voer SPARQL UPDATE (INSERT/DELETE) uit."""
        try:
            self.graph.update(sparql, initNs={
                "vakantie": VAKANTIE,
                "data": DATA,
                "xsd": XSD,
            })
            return {"success": True, "triples_total": len(self.graph)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ── Laag 1: Regex pre-validatie ─────────────────────────────
    def validate_sparql_update(self, sparql: str, action_type: str = None, role: str = "klant") -> dict:
        """
        Pre-validatie vóór SPARQL uitvoering.
        Controleert:
        1. Rol mag dit Action Type gebruiken (via vakantie:allowedRole)
        2. Gerefereerde entiteiten bestaan in de graph
        3. Aangemaakte types zijn toegestaan (readOnly check tenzij admin)
        4. beschikbareKamers > 0 bij MaakBoeking
        """
        # Vind entiteiten die worden AANGEMAAKT (data:xxx a vakantie:Yyy)
        creation_pattern = r'data:(\w+)\s+a\s+vakantie:(\w+)'
        creations = re.findall(creation_pattern, sparql)
        created_uris = {uri for uri, cls in creations}
        created_types = {cls for uri, cls in creations}

        # Vind ALLE data: URIs in de query
        all_data_uris = set(re.findall(r'data:(\w+)', sparql))

        # Gerefereerde URIs = gebruikt maar niet aangemaakt → moeten bestaan
        referenced_uris = all_data_uris - created_uris

        # Check 1: mag deze rol dit Action Type gebruiken?
        if action_type:
            allowed_roles = self._get_allowed_roles(action_type)
            if allowed_roles and role not in allowed_roles:
                return {
                    "valid": False,
                    "reason": f"Rol '{role}' mag Action Type '{action_type}' niet gebruiken. "
                              f"Toegestane rollen: {', '.join(allowed_roles)}.",
                }

        # Check 2: bestaan gerefereerde entiteiten?
        missing = []
        for local_name in referenced_uris:
            full_uri = URIRef(str(DATA) + local_name)
            if not list(self.graph.objects(full_uri, RDF.type)):
                missing.append(f"data:{local_name}")

        if missing:
            return {
                "valid": False,
                "reason": f"De volgende entiteiten bestaan niet in de database: {', '.join(missing)}. "
                          f"Doe eerst een SELECT om bestaande entiteiten te vinden, "
                          f"of vraag de gebruiker om de juiste naam.",
                "missing": missing,
            }

        # Check 3: mag dit type aangemaakt worden?
        for created_type in created_types:
            type_uri = VAKANTIE[created_type]
            is_read_only = Literal(True) in self.graph.objects(type_uri, VAKANTIE["readOnly"])
            if is_read_only and role != "admin":
                return {
                    "valid": False,
                    "reason": f"'{created_type}' is read-only en kan niet aangemaakt worden "
                              f"met rol '{role}'. Alleen admins mogen dit type aanmaken.",
                }

        # Check 4: beschikbareKamers bij MaakBoeking
        if action_type == "MaakBoeking":
            hotel_refs = re.findall(r'vakantie:isGeboektIn\s+data:(\w+)', sparql)
            for hotel_local in hotel_refs:
                hotel_uri = str(DATA) + hotel_local
                result = self.query(f"""
                    SELECT ?kamers WHERE {{
                        <{hotel_uri}> vakantie:beschikbareKamers ?kamers .
                    }}
                """)
                if result["success"] and result["results"]:
                    kamers = int(result["results"][0].get("kamers", 0))
                    if kamers < 1:
                        return {
                            "valid": False,
                            "reason": f"Hotel data:{hotel_local} heeft geen kamers beschikbaar "
                                      f"(ontologie constraint: beschikbareKamers > 0).",
                        }

        return {"valid": True}

    def _get_allowed_creation_types(self, action_type: str) -> set:
        """Lees vakantie:createsType uit de ontologie voor een Action Type."""
        action_uri = VAKANTIE[action_type]
        creates_prop = VAKANTIE["createsType"]
        return {
            str(obj).replace(str(VAKANTIE), "")
            for obj in self.graph.objects(action_uri, creates_prop)
        }

    def _get_allowed_roles(self, action_type: str) -> set:
        """Lees vakantie:allowedRole uit de ontologie voor een Action Type."""
        action_uri = VAKANTIE[action_type]
        return {
            str(obj) for obj in self.graph.objects(action_uri, VAKANTIE["allowedRole"])
        }

    # ── Laag 2: SHACL post-validatie ─────────────────────────────
    def validate_graph_shacl(self) -> dict:
        """
        Valideer de volledige graph tegen de SHACL shapes.
        Geeft violations terug als de graph niet valide is.
        """
        conforms, results_graph, results_text = shacl_validate(
            self.graph,
            shacl_graph=self.shacl_graph,
            inference="none",
        )
        if conforms:
            return {"valid": True}

        # Parse violations uit het resultaat
        violations = []
        SH = Namespace("http://www.w3.org/ns/shacl#")
        for result in results_graph.subjects(RDF.type, SH.ValidationResult):
            message = str(results_graph.value(result, SH.resultMessage) or "Onbekende fout")
            focus = str(results_graph.value(result, SH.focusNode) or "")
            focus = focus.replace(str(DATA), "data:").replace(str(VAKANTIE), "vakantie:")
            violations.append(f"{focus}: {message}")

        return {
            "valid": False,
            "violations": violations,
            "reason": "SHACL validatie gefaald: " + "; ".join(violations),
        }

    def snapshot(self) -> bytes:
        """Maak een snapshot van de huidige graph state (voor rollback)."""
        return self.graph.serialize(format="ntriples").encode("utf-8")

    def restore(self, snapshot_data: bytes):
        """Herstel de graph vanuit een snapshot."""
        self.graph = Graph()
        self.graph.bind("vakantie", VAKANTIE)
        self.graph.bind("data", DATA)
        self.graph.bind("schema", SCHEMA)
        self.graph.bind("foaf", FOAF)
        self.graph.parse(data=snapshot_data, format="ntriples")

    def dump_turtle(self) -> str:
        """Export huidige state als Turtle — handig voor debugging."""
        return self.graph.serialize(format="turtle")


# ═══════════════════════════════════════════════════════════════
#  AGENT TOOLS  (nu generiek: alleen SPARQL)
#  Dit is de kern van de architectuurverschuiving:
#  Geen hardcoded business logic meer — de agent genereert SPARQL
# ═══════════════════════════════════════════════════════════════
AGENT_TOOLS = [
    {
        "name": "sparql_select",
        "description": """Voer een SPARQL SELECT query uit op de Vakantie triplestore.
        Gebruik de ontologie namespaces: vakantie: en data:
        Voorbeeld:
          SELECT ?naam ?status WHERE {
            ?klant a vakantie:Klant ; vakantie:naam ?naam .
            ?klant vakantie:heeftBoeking ?boeking .
            ?boeking vakantie:status ?status .
          }
        """,
        "input_schema": {
            "type": "object",
            "properties": {
                "sparql": {
                    "type": "string",
                    "description": "Volledige SPARQL SELECT query string"
                }
            },
            "required": ["sparql"]
        }
    },
    {
        "name": "sparql_update",
        "description": """Voer een SPARQL UPDATE (INSERT DATA / DELETE-INSERT) uit.
        Gebruik voor aanmaken, wijzigen of verwijderen van triples.
        
        Voorbeeld INSERT:
          INSERT DATA {
            data:boekingX a vakantie:Boeking ;
              vakantie:status "bevestigd" ;
              vakantie:totaalprijs 1400 .
            data:klant1 vakantie:heeftBoeking data:boekingX .
          }
        
        Voorbeeld DELETE-INSERT (update):
          DELETE { ?boeking vakantie:status ?oudeStatus }
          INSERT { ?boeking vakantie:status "geannuleerd" }
          WHERE  { ?boeking a vakantie:Boeking ; vakantie:status ?oudeStatus .
                   FILTER(?boeking = data:boeking4) }
        
        Genereer altijd unieke data: URIs voor nieuwe entiteiten (data:boeking<uuid>).
        """,
        "input_schema": {
            "type": "object",
            "properties": {
                "sparql": {
                    "type": "string",
                    "description": "Volledige SPARQL UPDATE string"
                },
                "action_type": {
                    "type": "string",
                    "description": "Optioneel: ontologie Action Type voor validatie (MaakBoeking, AnnuleerBoeking, UpdateLoyalty)"
                }
            },
            "required": ["sparql"]
        }
    },
    {
        "name": "get_ontology",
        "description": "Haal de volledige OWL ontologie op als Turtle. Gebruik dit als je onzeker bent over de property namen of klassenstructuur.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


# ═══════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
#  Minder data, meer ontologie-structuur
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT_BASE = """
Je bent een intelligente database-agent voor Vakantie BV.
Je communiceert met een RDF triplestore via SPARQL.

## ONTOLOGIE NAMESPACES
- vakantie: <https://vakantie.nl/ontology#>  (klassen, properties, actions)
- data:     <https://vakantie.nl/data#>       (individuele instanties)

## KLASSEN
- vakantie:Klant       (foaf:Person)         — klantId, naam, email, loyaltyPunten
- vakantie:Hotel       (schema:LodgingBusiness) — naam, sterren, prijsPerNacht, beschikbareKamers
- vakantie:Bestemming  (schema:Place)         — naam, land, klimaat
- vakantie:Boeking     (schema:Reservation)   — checkIn, checkOut, aantalPersonen, status, totaalprijs

## OBJECT PROPERTIES
- vakantie:heeftBoeking   : Klant → Boeking
- vakantie:isGeboektIn    : Boeking → Hotel
- vakantie:isGevestigdIn  : Hotel → Bestemming

## SPARQL INSTRUCTIES
1. Gebruik altijd PREFIX declaraties in je queries
2. Multi-hop navigatie gaat via chaining: ?klant → ?boeking → ?hotel → ?bestemming
3. Voor nieuwe entiteiten: genereer data:<type><uuid4_eerste_8_chars> als URI
4. Bij MaakBoeking: doe eerst een SELECT om beschikbareKamers te controleren
5. Bij schrijven: geef action_type mee zodat ontologie-validatie kan plaatsvinden
6. Antwoord in het Nederlands
7. Verzin NOOIT waarden voor properties die de gebruiker niet heeft opgegeven.
   Als informatie ontbreekt, VRAAG het aan de gebruiker.

SPARQL PREFIX blok (gebruik altijd):
PREFIX vakantie: <https://vakantie.nl/ontology#>
PREFIX data:     <https://vakantie.nl/data#>
PREFIX xsd:      <http://www.w3.org/2001/XMLSchema#>

## VALIDATIE
Het systeem valideert je SPARQL met twee lagen:
1. Pre-check: bestaan alle gerefereerde entiteiten? Mag jouw rol dit type aanmaken?
2. Post-check: voldoet de graph aan SHACL shapes?
Als een validatie faalt, krijg je een foutmelding — pas je query aan op basis daarvan.
"""

SYSTEM_PROMPT_KLANT = SYSTEM_PROMPT_BASE + """
## JOUW ROL: KLANT
Je helpt klanten met boekingen. Je hebt beperkte rechten.

## ACTION TYPES
- vakantie:MaakBoeking     (action_type: "MaakBoeking")    : maak een boeking aan
- vakantie:AnnuleerBoeking (action_type: "AnnuleerBoeking"): annuleer een boeking
- vakantie:UpdateLoyalty   (action_type: "UpdateLoyalty")   : wijzig loyaltypunten

## RESTRICTIES
- Maak NOOIT nieuwe Hotels, Bestemmingen of Klanten aan — deze zijn read-only.
- Hotel en Klant MOETEN al bestaan. Doe eerst een SELECT om te controleren.
- Als een hotel niet in het systeem staat, toon de beschikbare hotels.
- Geef ALTIJD action_type mee bij sparql_update.
"""

SYSTEM_PROMPT_ADMIN = SYSTEM_PROMPT_BASE + """
## JOUW ROL: ADMIN
Je bent een systeembeheerder. Je mag alle entiteiten aanmaken en wijzigen.

## ACTION TYPES
- vakantie:MaakBoeking     (action_type: "MaakBoeking")      : maak een boeking aan
- vakantie:AnnuleerBoeking (action_type: "AnnuleerBoeking")   : annuleer een boeking
- vakantie:UpdateLoyalty   (action_type: "UpdateLoyalty")      : wijzig loyaltypunten
- vakantie:MaakKlant       (action_type: "MaakKlant")         : maak een nieuwe klant aan
- vakantie:MaakHotel       (action_type: "MaakHotel")         : maak een nieuw hotel aan (vereist bestaande Bestemming)
- vakantie:MaakBestemming  (action_type: "MaakBestemming")    : maak een nieuwe bestemming aan

## VEREISTE VELDEN BIJ AANMAKEN
- Klant: naam, email, loyaltyPunten (standaard 0), klantId
- Hotel: naam, sterren, prijsPerNacht, beschikbareKamers, isGevestigdIn (bestaande Bestemming)
- Bestemming: naam, land, klimaat
- Boeking: checkIn, checkOut, aantalPersonen, status, totaalprijs, isGeboektIn, heeftBoeking

## REGELS
- Verzin NOOIT waarden die de gebruiker niet heeft opgegeven — VRAAG ernaar.
- Bij MaakHotel: de Bestemming MOET al bestaan (of maak die eerst aan).
- Geef ALTIJD action_type mee bij sparql_update.
- Genereer een nieuw klantId (volgnummer) voor nieuwe klanten.
"""

def get_system_prompt(role: str) -> str:
    if role == "admin":
        return SYSTEM_PROMPT_ADMIN
    return SYSTEM_PROMPT_KLANT


# ═══════════════════════════════════════════════════════════════
#  AGENT LOOP
# ═══════════════════════════════════════════════════════════════
class VakantieAgent:
    def __init__(self, triplestore: VakantieTriplestore):
        self.store = triplestore
        self.client = anthropic.Anthropic()
        self.history = []
        self.role = "klant"
    
    def _execute_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Voert een tool call uit tegen de triplestore."""
        
        if tool_name == "sparql_select":
            sparql = tool_input["sparql"]
            print(f"\n  🔍 SPARQL SELECT:\n{self._indent(sparql)}")
            result = self.store.query(sparql)
            print(f"  → {result['count']} resultaten" if result["success"] else f"  → FOUT: {result['error']}")
            return result
        
        elif tool_name == "sparql_update":
            sparql = tool_input["sparql"]
            action_type = tool_input.get("action_type")
            print(f"\n  ✏️  SPARQL UPDATE:\n{self._indent(sparql)}")

            # ── Laag 1: Regex pre-validatie ──────────────────────
            pre_check = self.store.validate_sparql_update(sparql, action_type, self.role)
            if not pre_check["valid"]:
                print(f"  ✗ Pre-validatie: {pre_check['reason']}")
                return {"success": False, "error": pre_check["reason"]}

            # ── Snapshot voor rollback ───────────────────────────
            snapshot = self.store.snapshot()

            # ── Voer SPARQL UPDATE uit ──────────────────────────
            result = self.store.update(sparql)
            if not result["success"]:
                print(f"  ✗ SPARQL fout: {result.get('error','')}")
                return result

            # ── Laag 2: SHACL post-validatie ────────────────────
            shacl_check = self.store.validate_graph_shacl()
            if not shacl_check["valid"]:
                print(f"  ✗ SHACL validatie gefaald — ROLLBACK")
                for v in shacl_check.get("violations", []):
                    print(f"    • {v}")
                self.store.restore(snapshot)
                return {"success": False, "error": shacl_check["reason"]}

            print(f"  ✓ Geschreven + SHACL valide ({result['triples_total']} triples)")
            return result
        
        elif tool_name == "get_ontology":
            print(f"\n  📖 Ontologie opgevraagd")
            return {"ontology": ONTOLOGY_TTL}
        
        return {"success": False, "error": f"Onbekende tool: {tool_name}"}
    
    def _indent(self, text: str, prefix: str = "    ") -> str:
        return "\n".join(prefix + line for line in text.strip().split("\n"))
    
    def chat(self, user_message: str, role: str = None) -> str:
        """Verwerk een gebruikersbericht via de agent loop."""
        if role:
            self.role = role
        print(f"\n{'═'*60}")
        print(f"👤 [{self.role.upper()}] Gebruiker: {user_message}")
        print(f"{'═'*60}")

        self.history.append({"role": "user", "content": user_message})

        while True:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=get_system_prompt(self.role),
                tools=AGENT_TOOLS,
                messages=self.history,
            )
            
            # Verwerk response
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]
            
            # Voeg assistant turn toe aan history
            self.history.append({"role": "assistant", "content": response.content})
            
            if not tool_uses or response.stop_reason == "end_turn":
                # Geen tools meer — geef tekst antwoord
                final_text = "\n".join(b.text for b in text_blocks)
                if final_text:
                    print(f"\n🤖 Agent: {final_text}")
                return final_text
            
            # Voer tools uit en stuur resultaten terug
            tool_results = []
            for tu in tool_uses:
                print(f"\n⚡ Tool: {tu.name}")
                result = self._execute_tool(tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
            
            self.history.append({"role": "user", "content": tool_results})


# ═══════════════════════════════════════════════════════════════
#  FASTAPI SERVER  (voor de HTML demo frontend)
# ═══════════════════════════════════════════════════════════════
def create_api_server(store: VakantieTriplestore):
    """
    FastAPI server die de HTML frontend verbindt met de RDFLib triplestore.
    
    Endpoints:
      POST /chat         — agent chat (zelfde interface als Anthropic API)
      GET  /graph        — huidige triplestore als JSON-LD
      GET  /sparql       — directe SPARQL query (debug)
    """
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel
    except ImportError:
        print("FastAPI niet geïnstalleerd. Run: pip install fastapi uvicorn")
        return None
    
    app = FastAPI(title="Vakantie BV Ontology API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    
    agent = VakantieAgent(store)
    
    class ChatRequest(BaseModel):
        message: str
        role: str = "klant"
        reset: bool = False

    class SparqlRequest(BaseModel):
        query: str

    @app.post("/chat")
    async def chat_endpoint(req: ChatRequest):
        if req.reset:
            agent.history = []
        response = agent.chat(req.message, role=req.role)
        return {"response": response, "role": req.role, "history_length": len(agent.history)}
    
    @app.post("/sparql/select")
    async def sparql_select(req: SparqlRequest):
        return store.query(req.query)
    
    @app.post("/sparql/update")
    async def sparql_update(req: SparqlRequest):
        return store.update(req.query)
    
    @app.get("/graph/turtle")
    async def get_turtle():
        return {"turtle": store.dump_turtle()}
    
    @app.get("/state")
    async def get_state():
        """Geeft de huidige triplestore state terug als JSON tabellen voor de frontend."""
        klanten = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            SELECT ?klant_id ?naam ?email ?loyalty_punten WHERE {
                ?k a vakantie:Klant ;
                   vakantie:klantId ?klant_id ;
                   vakantie:naam ?naam ;
                   vakantie:email ?email ;
                   vakantie:loyaltyPunten ?loyalty_punten .
            } ORDER BY ?klant_id
        """)
        bestemmingen = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            SELECT ?naam ?land ?klimaat WHERE {
                ?b a vakantie:Bestemming ;
                   vakantie:naam ?naam ;
                   vakantie:land ?land ;
                   vakantie:klimaat ?klimaat .
            }
        """)
        hotels = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            SELECT ?naam ?sterren ?prijs ?kamers ?bestemming WHERE {
                ?h a vakantie:Hotel ;
                   vakantie:naam ?naam ;
                   vakantie:sterren ?sterren ;
                   vakantie:prijsPerNacht ?prijs ;
                   vakantie:beschikbareKamers ?kamers ;
                   vakantie:isGevestigdIn ?best .
                ?best vakantie:naam ?bestemming .
            }
        """)
        boekingen = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            SELECT ?klant ?hotel ?check_in ?check_out ?personen ?status ?totaalprijs WHERE {
                ?b a vakantie:Boeking ;
                   vakantie:checkIn ?check_in ;
                   vakantie:checkOut ?check_out ;
                   vakantie:aantalPersonen ?personen ;
                   vakantie:status ?status ;
                   vakantie:totaalprijs ?totaalprijs ;
                   vakantie:isGeboektIn ?h .
                ?h vakantie:naam ?hotel .
                ?k vakantie:heeftBoeking ?b ;
                   vakantie:naam ?klant .
            }
        """)
        return {
            "klanten": klanten.get("results", []),
            "bestemmingen": bestemmingen.get("results", []),
            "hotels": hotels.get("results", []),
            "boekingen": boekingen.get("results", []),
        }

    @app.get("/health")
    async def health():
        return {"status": "ok", "triples": len(store.graph)}

    return app


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    store = VakantieTriplestore()
    
    if "--cli" in sys.argv:
        # Interactieve CLI modus
        agent = VakantieAgent(store)
        print("\n🌊 Vakantie BV — RDF Ontology Agent")
        print("Typ 'stop' om te stoppen, 'turtle' om triplestore te dumpen\n")
        
        # Demo queries
        demo_vragen = [
            "Wat zijn alle boekingen van Sofia Martins?",
            "Welke hotels in Bali hebben nog kamers beschikbaar?",
            "Boek voor Lars van den Berg (klant 4) het Komaneka at Bisma in Bali van 15 september tot 22 september 2025 voor 2 personen.",
        ]
        
        print("Demo vragen (druk Enter om te bevestigen of typ zelf):")
        for i, v in enumerate(demo_vragen, 1):
            print(f"  {i}. {v}")
        print()
        
        while True:
            try:
                user_input = input("👤 > ").strip()
            except (KeyboardInterrupt, EOFError):
                break
            
            if user_input.lower() == "stop":
                break
            elif user_input.lower() == "turtle":
                print(store.dump_turtle())
                continue
            elif user_input.isdigit() and 1 <= int(user_input) <= len(demo_vragen):
                user_input = demo_vragen[int(user_input) - 1]
            
            if user_input:
                agent.chat(user_input)
    
    else:
        # FastAPI server modus
        try:
            import uvicorn
            app = create_api_server(store)
            if app:
                print("\n🌊 Vakantie BV API Server")
                print("   http://localhost:8000/docs  (Swagger UI)")
                print("   http://localhost:8000/health")
                print("\nVerbind de HTML frontend met: http://localhost:8000/chat\n")
                uvicorn.run(app, host="0.0.0.0", port=8000)
        except ImportError:
            print("Uvicorn niet geïnstalleerd. Run: pip install fastapi uvicorn")
            print("Of gebruik: python vakantie_rdf_backend.py --cli")
