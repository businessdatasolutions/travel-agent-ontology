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
    vakantie:mapsTo "klanten" ;
    vakantie:primaryKey "klant_id" ;
    rdfs:comment "Een reizende klant van Vakantie BV — beheerd door administratie"@nl .

vakantie:Hotel a owl:Class ;
    rdfs:label "Hotel"@nl ;
    rdfs:subClassOf schema:LodgingBusiness ;
    vakantie:readOnly true ;
    vakantie:mapsTo "hotels" ;
    vakantie:primaryKey "hotel_id" ;
    rdfs:comment "Een accommodatie op een bestemming — beheerd door administratie"@nl .

vakantie:Bestemming a owl:Class ;
    rdfs:label "Bestemming"@nl ;
    rdfs:subClassOf schema:Place ;
    vakantie:readOnly true ;
    vakantie:mapsTo "bestemmingen" ;
    vakantie:primaryKey "bestemming_id" ;
    rdfs:comment "Een reisbestemming op de wereld — beheerd door administratie"@nl .

vakantie:Boeking a owl:Class ;
    rdfs:label "Boeking"@nl ;
    rdfs:subClassOf schema:Reservation ;
    vakantie:mapsTo "boekingen" ;
    vakantie:primaryKey "boeking_id" ;
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

vakantie:hotelId a owl:DatatypeProperty ;
    rdfs:domain vakantie:Hotel ; rdfs:range xsd:integer .

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
    vakantie:allowedRole "reisagent" ;
    vakantie:allowedRole "admin" ;
    vakantie:precondition "beschikbareKamers > 0" ;
    vakantie:sideEffect "beschikbareKamers - 1" ;
    vakantie:sideEffect "loyaltyPunten + (totaalprijs / 100 * 10)" .

vakantie:AnnuleerBoeking a vakantie:ActionType ;
    rdfs:label "Annuleer Boeking"@nl ;
    vakantie:modifiesType vakantie:Boeking ;
    vakantie:setsProperty vakantie:status ;
    vakantie:allowedRole "reisagent" ;
    vakantie:allowedRole "admin" ;
    vakantie:sideEffect "beschikbareKamers + 1" .

vakantie:UpdateLoyalty a vakantie:ActionType ;
    rdfs:label "Update Loyalty Punten"@nl ;
    vakantie:modifiesType vakantie:Klant ;
    vakantie:setsProperty vakantie:loyaltyPunten ;
    vakantie:allowedRole "reisagent" ;
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
        sh:path [ sh:inversePath vakantie:heeftBoeking ] ;
        sh:class vakantie:Klant ;
        sh:minCount 1 ;
        sh:maxCount 1 ;
        sh:message "Boeking moet gekoppeld zijn aan een Klant via heeftBoeking"@nl ;
    ] ;
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

# ── Bestemming: verplichte velden ────────────────────────────
vakantie:BestemmingShape a sh:NodeShape ;
    sh:targetClass vakantie:Bestemming ;
    sh:property [
        sh:path vakantie:naam ;
        sh:minCount 1 ;
        sh:message "Bestemming moet een naam hebben"@nl ;
    ] ;
    sh:property [
        sh:path vakantie:land ;
        sh:minCount 1 ;
        sh:message "Bestemming moet een land hebben"@nl ;
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
    vakantie:hotelId 1 ;
    vakantie:naam "Hotel Arts" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 320 ;
    vakantie:beschikbareKamers 12 ;
    vakantie:isGevestigdIn data:barcelona .

data:hotel2 a vakantie:Hotel ;
    vakantie:hotelId 2 ;
    vakantie:naam "Catalonia Square" ;
    vakantie:sterren 4 ;
    vakantie:prijsPerNacht 145 ;
    vakantie:beschikbareKamers 8 ;
    vakantie:isGevestigdIn data:barcelona .

data:hotel3 a vakantie:Hotel ;
    vakantie:hotelId 3 ;
    vakantie:naam "COMO Uma Ubud" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 280 ;
    vakantie:beschikbareKamers 6 ;
    vakantie:isGevestigdIn data:bali .

data:hotel4 a vakantie:Hotel ;
    vakantie:hotelId 4 ;
    vakantie:naam "Komaneka at Bisma" ;
    vakantie:sterren 4 ;
    vakantie:prijsPerNacht 195 ;
    vakantie:beschikbareKamers 15 ;
    vakantie:isGevestigdIn data:bali .

data:hotel5 a vakantie:Hotel ;
    vakantie:hotelId 5 ;
    vakantie:naam "La Mamounia" ;
    vakantie:sterren 5 ;
    vakantie:prijsPerNacht 410 ;
    vakantie:beschikbareKamers 3 ;
    vakantie:isGevestigdIn data:marrakech .

data:hotel6 a vakantie:Hotel ;
    vakantie:hotelId 6 ;
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
    
    # ── Laag 1: Ontologie-gedreven pre-validatie ─────────────────
    def validate_sparql_update(self, sparql: str, action_type: str = None, role: str = "reisagent") -> dict:
        """
        Pre-validatie vóór SPARQL uitvoering.
        Alle checks zijn afgeleid uit de ontologie — geen hardcoded business logic.

        Controleert:
        1. vakantie:allowedRole — mag deze rol dit ActionType gebruiken?
        2. Referentiële integriteit — bestaan gerefereerde entiteiten?
        3. vakantie:readOnly — mag dit type aangemaakt worden door deze rol?
        4. vakantie:precondition — voldoen dynamische voorwaarden?
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

        # Check 1: vakantie:allowedRole
        if action_type:
            allowed_roles = self._get_allowed_roles(action_type)
            if allowed_roles and role not in allowed_roles:
                return {
                    "valid": False,
                    "reason": f"Ontologie-constraint: vakantie:{action_type} heeft "
                              f"vakantie:allowedRole {sorted(allowed_roles)}. "
                              f"Huidige rol '{role}' is niet toegestaan.",
                    "constraint": f"vakantie:{action_type} vakantie:allowedRole",
                }

        # Check 2: referentiële integriteit
        missing = []
        for local_name in referenced_uris:
            full_uri = URIRef(str(DATA) + local_name)
            if not list(self.graph.objects(full_uri, RDF.type)):
                missing.append(f"data:{local_name}")

        if missing:
            return {
                "valid": False,
                "reason": f"Entiteiten niet gevonden: {', '.join(missing)}. "
                          f"Gebruik SELECT om bestaande entiteiten op te zoeken.",
                "missing": missing,
            }

        # Check 3: vakantie:readOnly
        for created_type in created_types:
            type_uri = VAKANTIE[created_type]
            is_read_only = Literal(True) in self.graph.objects(type_uri, VAKANTIE["readOnly"])
            if is_read_only and role != "admin":
                return {
                    "valid": False,
                    "reason": f"Ontologie-constraint: vakantie:{created_type} heeft "
                              f"vakantie:readOnly true. Rol '{role}' mag dit type niet aanmaken. "
                              f"Gebruik SELECT om bestaande {created_type}-instanties te vinden.",
                    "constraint": f"vakantie:{created_type} vakantie:readOnly true",
                }

        # Check 4: vakantie:precondition (generiek, uit ontologie)
        if action_type:
            preconditions = self._get_preconditions(action_type)
            for precond in preconditions:
                violation = self._evaluate_precondition(sparql, precond)
                if violation:
                    return {
                        "valid": False,
                        "reason": f"Ontologie-precondition niet vervuld: "
                                  f"vakantie:{action_type} vereist \"{precond}\". {violation}",
                        "constraint": f"vakantie:{action_type} vakantie:precondition \"{precond}\"",
                    }

        return {"valid": True}

    # ── Laag 1.5: Automatische verrijking vanuit ontologie ────────
    def enrich_sparql(self, sparql: str, action_type: str) -> str:
        """
        Verrijkt SPARQL INSERT automatisch vanuit de ontologie:
        1. Ontbrekende relatie-triples (ObjectProperties) injecteren
        2. Ontbrekende primary keys (auto-increment) toekennen

        Dit maakt het systeem deterministisch — ongeacht wat de LLM genereert,
        de ontologie-constraints worden altijd nagekomen.
        """
        prefix = str(VAKANTIE)
        action_uri = VAKANTIE[action_type]

        def shorten(uri):
            s = str(uri)
            return s.replace(prefix, "") if prefix in s else s

        # ── Parse de SPARQL ──────────────────────────────────────
        # Alleen entiteiten uit INSERT DATA blokken zijn echte creaties
        # (niet uit WHERE of DELETE clauses)
        insert_match = re.search(r'INSERT\s+DATA\s*\{(.*)\}', sparql, re.DOTALL | re.IGNORECASE)
        insert_body = insert_match.group(1) if insert_match else ""
        creation_pattern = r'data:(\w+)\s+a\s+vakantie:(\w+)'
        creations = re.findall(creation_pattern, insert_body)
        uri_to_type = {}  # type → local URI
        type_to_uri = {}  # local URI → type
        for uri, cls in creations:
            uri_to_type[cls] = uri
            type_to_uri[uri] = cls

        # Voor gerefereerde URIs: zoek hun type op in de graph
        all_data_uris = set(re.findall(r'data:(\w+)', sparql))
        created_uris = set(uri_to_type.values())
        for local_name in all_data_uris - created_uris:
            full_uri = URIRef(str(DATA) + local_name)
            for rdf_type in self.graph.objects(full_uri, RDF.type):
                type_name = shorten(rdf_type)
                if type_name not in uri_to_type:
                    uri_to_type[type_name] = local_name
                    type_to_uri[local_name] = type_name

        # ── 1. Auto-increment primary keys ───────────────────────
        # Net als een relationele database: het systeem kent IDs toe,
        # niet de gebruiker of de LLM. Agent-gegenereerde IDs worden vervangen.
        for uri, cls in creations:
            cls_uri = VAKANTIE[cls]
            pk_values = list(self.graph.objects(cls_uri, VAKANTIE["primaryKey"]))
            if not pk_values:
                continue
            pk_column = str(pk_values[0])  # bijv. "klant_id"
            pk_prop = pk_column.replace("_i", "I").replace("_", "")  # klant_id → klantId

            # Alleen auto-ID als de DatatypeProperty in de ontologie bestaat
            if (VAKANTIE[pk_prop], RDF.type, OWL.DatatypeProperty) not in self.graph:
                continue

            # Verwijder een eventueel door de agent meegegeven ID (altijd overschrijven)
            agent_pk = rf'vakantie:{re.escape(pk_prop)}\s+[^\s;.]+\s*[;.]'
            sparql = re.sub(agent_pk, '', sparql)

            # Bepaal het volgende ID via auto-increment
            max_result = self.query(f"""
                PREFIX vakantie: <{prefix}>
                SELECT (MAX(?id) AS ?maxId) WHERE {{
                    ?x a vakantie:{cls} ;
                       vakantie:{pk_prop} ?id .
                }}
            """)
            max_id = 0
            if max_result.get("results"):
                val = max_result["results"][0].get("maxId")
                if val is not None:
                    try:
                        max_id = int(val)
                    except (ValueError, TypeError):
                        max_id = 0
            next_id = max_id + 1

            # Injecteer na "data:uri a vakantie:Type ;"
            triple = f"\n      vakantie:{pk_prop} {next_id} ;"
            type_pattern = rf'(data:{re.escape(uri)}\s+a\s+vakantie:{re.escape(cls)}\s*;)'
            match = re.search(type_pattern, sparql)
            if match:
                sparql = sparql[:match.end()] + triple + sparql[match.end():]
                print(f"  🔢 Auto-ID: data:{uri} vakantie:{pk_prop} {next_id}")

        # ── 2. Ontbrekende relatie-triples injecteren ─────────────
        creates = {shorten(o) for o in self.graph.objects(action_uri, VAKANTIE["createsType"])}
        requires = {shorten(o) for o in self.graph.objects(action_uri, VAKANTIE["requiresInput"])}

        if not creates:
            return sparql

        all_types = creates | requires
        obj_props = self.graph.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop ?domain ?range WHERE {{
                ?prop a owl:ObjectProperty ;
                      rdfs:domain ?domain ;
                      rdfs:range ?range .
            }}
        """)

        for row in obj_props:
            prop = shorten(row.prop)
            domain = shorten(row.domain)
            range_ = shorten(row.range)

            if domain not in all_types or range_ not in all_types:
                continue

            domain_uri = uri_to_type.get(domain)
            range_uri = uri_to_type.get(range_)

            if not domain_uri or not range_uri:
                continue

            # Check of de relatie-triple al in de SPARQL staat
            triple_pattern = rf'data:{re.escape(domain_uri)}\s+vakantie:{re.escape(prop)}\s+data:{re.escape(range_uri)}'
            if re.search(triple_pattern, sparql):
                continue

            triple = f"\n      data:{domain_uri} vakantie:{prop} data:{range_uri} ."
            last_brace = sparql.rfind("}")
            if last_brace != -1:
                sparql = sparql[:last_brace] + triple + "\n    " + sparql[last_brace:]
                print(f"  ⚡ Auto-relatie: data:{domain_uri} vakantie:{prop} data:{range_uri}")

        return sparql

    # ── Laag 2: SPARQL-generatie vanuit ActionType ────────────────
    def generate_action_sparql(self, action_type: str, params: dict, references: dict = None) -> dict:
        """
        Genereert deterministische SPARQL INSERT vanuit een ActionType + parameters.
        Het systeem leest de ontologie en bouwt de correcte query op.

        Returns: {"sparql": "...", "uri": "data:...", "action_type": "..."} of {"error": "..."}
        """
        import uuid
        prefix = str(VAKANTIE)
        action_uri = VAKANTIE[action_type]
        references = references or {}

        def shorten(uri):
            s = str(uri)
            return s.replace(prefix, "") if prefix in s else s

        # 1. Lees ActionType metadata
        creates_list = [shorten(o) for o in self.graph.objects(action_uri, VAKANTIE["createsType"])]
        requires_list = [shorten(o) for o in self.graph.objects(action_uri, VAKANTIE["requiresInput"])]

        if not creates_list:
            return {"error": f"ActionType {action_type} heeft geen createsType"}

        target_class = creates_list[0]  # bijv. "Boeking"
        target_class_uri = VAKANTIE[target_class]

        # 2. Genereer URI
        short_uuid = str(uuid.uuid4())[:8]
        entity_uri = f"{target_class.lower()}_{short_uuid}"

        # 3. Auto-increment primary key
        pk_values = list(self.graph.objects(target_class_uri, VAKANTIE["primaryKey"]))
        pk_triple = ""
        if pk_values:
            pk_column = str(pk_values[0])
            pk_prop = pk_column.replace("_i", "I").replace("_", "")
            if (VAKANTIE[pk_prop], RDF.type, OWL.DatatypeProperty) in self.graph:
                max_result = self.query(f"""
                    PREFIX vakantie: <{prefix}>
                    SELECT (MAX(?id) AS ?maxId) WHERE {{
                        ?x a vakantie:{target_class} ;
                           vakantie:{pk_prop} ?id .
                    }}
                """)
                max_id = 0
                if max_result.get("results"):
                    val = max_result["results"][0].get("maxId")
                    if val is not None:
                        try:
                            max_id = int(val)
                        except (ValueError, TypeError):
                            pass
                pk_triple = f"\n    vakantie:{pk_prop} {max_id + 1} ;"

        # 4. Lees DatatypeProperties voor deze klasse (inclusief domain-loze zoals 'naam')
        props_raw = self.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop ?range WHERE {{
                ?prop a owl:DatatypeProperty .
                OPTIONAL {{ ?prop rdfs:domain ?domain }}
                OPTIONAL {{ ?prop rdfs:range ?range }}
                FILTER(!BOUND(?domain) || ?domain = vakantie:{target_class})
            }}
        """)

        # Bouw property triples op
        prop_triples = []
        for row in props_raw.get("results", []):
            prop_name = shorten(row["prop"]).replace("vakantie:", "")
            range_type = shorten(row.get("range", "string"))

            # Skip primary key (al afgehandeld)
            if pk_values and prop_name == pk_column.replace("_i", "I").replace("_", ""):
                continue

            value = params.get(prop_name)
            if value is None:
                # Default waarden voor ontbrekende properties
                if "integer" in range_type.lower():
                    value = 0
                else:
                    continue  # Skip optionele string-properties zonder waarde

            # Format de waarde met het juiste XSD type
            if "integer" in range_type.lower():
                prop_triples.append(f"    vakantie:{prop_name} {int(value)} ;")
            elif "decimal" in range_type.lower():
                prop_triples.append(f'    vakantie:{prop_name} "{value}"^^xsd:decimal ;')
            elif "date" in range_type.lower():
                prop_triples.append(f'    vakantie:{prop_name} "{value}"^^xsd:date ;')
            else:
                prop_triples.append(f'    vakantie:{prop_name} "{value}" ;')

        # 5. ObjectProperty triples vanuit references en ontologie
        relation_triples = []
        obj_props = self.graph.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop ?domain ?range WHERE {{
                ?prop a owl:ObjectProperty ;
                      rdfs:domain ?domain ;
                      rdfs:range ?range .
            }}
        """)

        for row in obj_props:
            prop = shorten(row.prop)
            domain = shorten(row.domain)
            range_ = shorten(row.range)

            if domain == target_class and range_ in references:
                # Forward relatie: nieuwe entiteit → bestaande entiteit
                ref_uri = references[range_]
                relation_triples.append(f"    vakantie:{prop} {ref_uri} ;")
            elif range_ == target_class and domain in references:
                # Inverse relatie: bestaande entiteit → nieuwe entiteit
                ref_uri = references[domain]
                relation_triples.append(f"  {ref_uri} vakantie:{prop} data:{entity_uri} .")

        # 6. Bouw de SPARQL INSERT DATA
        # Splits forward en inverse triples
        forward = [t for t in relation_triples if not t.strip().startswith("data:")]
        inverse = [t for t in relation_triples if t.strip().startswith("data:")]

        lines = [f"INSERT DATA {{"]
        lines.append(f"  data:{entity_uri} a vakantie:{target_class} ;")
        if pk_triple:
            lines.append(pk_triple)
        lines.extend(prop_triples)
        lines.extend(forward)

        # Vervang laatste ; door .
        combined = "\n".join(lines)
        last_semi = combined.rfind(";")
        if last_semi != -1:
            combined = combined[:last_semi] + "." + combined[last_semi+1:]

        # Voeg inverse relaties toe
        for inv in inverse:
            combined += "\n" + inv

        combined += "\n}"

        return {
            "sparql": combined,
            "uri": f"data:{entity_uri}",
            "action_type": action_type,
        }

    def _get_preconditions(self, action_type: str) -> list:
        """Lees vakantie:precondition uit de ontologie voor een ActionType."""
        action_uri = VAKANTIE[action_type]
        return [str(obj) for obj in self.graph.objects(action_uri, VAKANTIE["precondition"])]

    def _evaluate_precondition(self, sparql: str, precond: str) -> str | None:
        """
        Evalueer een ontologie-precondition tegen de huidige graph state.
        Ondersteunt het formaat: "propertyNaam > waarde"
        Retourneert een foutmelding als de precondition niet vervuld is, anders None.
        """
        match = re.match(r'(\w+)\s*(>|>=|<|<=|==|!=)\s*(\d+)', precond)
        if not match:
            return None  # Onbekend formaat — skip (SHACL vangt het op)

        prop_name, operator, threshold = match.groups()
        threshold = int(threshold)

        # Zoek de relevante entiteit in de SPARQL: check welk type deze property heeft
        # via ontologie rdfs:domain lookup
        domain_result = self.query(f"""
            PREFIX vakantie: <{str(VAKANTIE)}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?domain WHERE {{
                vakantie:{prop_name} rdfs:domain ?domain .
            }}
        """)
        if not domain_result.get("results"):
            return None

        domain_type = str(domain_result["results"][0]["domain"]).replace(str(VAKANTIE), "")

        # Zoek data:-URIs van dit type in de SPARQL (bijv. isGeboektIn data:hotelX → Hotel)
        # Heuristiek: zoek object properties die naar dit type verwijzen
        obj_prop_result = self.query(f"""
            PREFIX vakantie: <{str(VAKANTIE)}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop WHERE {{
                ?prop a owl:ObjectProperty ;
                      rdfs:range vakantie:{domain_type} .
            }}
        """)
        for row in obj_prop_result.get("results", []):
            prop_local = str(row["prop"]).replace(str(VAKANTIE), "")
            # Zoek in de SPARQL naar "vakantie:<prop> data:<entity>"
            entity_refs = re.findall(rf'vakantie:{prop_local}\s+data:(\w+)', sparql)
            for entity_local in entity_refs:
                entity_uri = str(DATA) + entity_local
                result = self.query(f"""
                    SELECT ?val WHERE {{
                        <{entity_uri}> vakantie:{prop_name} ?val .
                    }}
                """)
                if result["success"] and result["results"]:
                    val = int(result["results"][0].get("val", 0))
                    ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
                           "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                           "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
                    if not ops[operator](val, threshold):
                        return (f"data:{entity_local} heeft {prop_name}={val}, "
                                f"maar de precondition vereist {prop_name} {operator} {threshold}.")
        return None

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

    # ── Capability Compiler ──────────────────────────────────────
    def compile_capabilities(self, role: str) -> dict:
        """
        Bevraagt de ontologie via SPARQL en genereert een gestructureerd
        overzicht van wat een rol mag doen. Dit is de brug tussen de
        OWL ontologie en het agent system prompt.
        """
        prefix = str(VAKANTIE)

        def shorten(uri):
            s = str(uri)
            return s.replace(prefix, "") if prefix in s else s

        # 1. Toegestane ActionTypes voor deze rol
        actions_raw = self.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?action ?label ?creates ?modifies ?requires ?precond ?sideEffect ?setsProperty WHERE {{
                ?action a vakantie:ActionType ;
                        vakantie:allowedRole "{role}" .
                OPTIONAL {{ ?action rdfs:label ?label }}
                OPTIONAL {{ ?action vakantie:createsType ?creates }}
                OPTIONAL {{ ?action vakantie:modifiesType ?modifies }}
                OPTIONAL {{ ?action vakantie:requiresInput ?requires }}
                OPTIONAL {{ ?action vakantie:precondition ?precond }}
                OPTIONAL {{ ?action vakantie:sideEffect ?sideEffect }}
                OPTIONAL {{ ?action vakantie:setsProperty ?setsProperty }}
            }}
        """)

        # Groepeer per action (SPARQL geeft meerdere rijen bij multi-valued properties)
        actions = {}
        for row in actions_raw.get("results", []):
            name = shorten(row["action"])
            if name not in actions:
                actions[name] = {
                    "name": name,
                    "label": str(row.get("label", name)),
                    "creates": set(),
                    "modifies": set(),
                    "requires": set(),
                    "preconditions": set(),
                    "sideEffects": set(),
                    "setsProperties": set(),
                }
            a = actions[name]
            if row.get("creates"): a["creates"].add(shorten(row["creates"]))
            if row.get("modifies"): a["modifies"].add(shorten(row["modifies"]))
            if row.get("requires"): a["requires"].add(shorten(row["requires"]))
            if row.get("precond"): a["preconditions"].add(str(row["precond"]))
            if row.get("sideEffect"): a["sideEffects"].add(str(row["sideEffect"]))
            if row.get("setsProperty"): a["setsProperties"].add(shorten(row["setsProperty"]))

        # Converteer sets naar lists voor JSON-serialisatie
        for a in actions.values():
            for k in ["creates", "modifies", "requires", "preconditions", "sideEffects", "setsProperties"]:
                a[k] = sorted(a[k])

        # 2. Read-only klassen
        readonly_raw = self.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?cls ?label WHERE {{
                ?cls a <http://www.w3.org/2002/07/owl#Class> ;
                     vakantie:readOnly true .
                OPTIONAL {{ ?cls rdfs:label ?label }}
            }}
        """)
        readonly = [
            {"id": shorten(r["cls"]), "label": str(r.get("label", shorten(r["cls"])))}
            for r in readonly_raw.get("results", [])
        ]

        # 3. Klassen-schema (datatype properties per klasse)
        props_raw = self.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop ?domain ?range WHERE {{
                ?prop a owl:DatatypeProperty ;
                      rdfs:domain ?domain .
                OPTIONAL {{ ?prop rdfs:range ?range }}
            }}
        """)
        schema = {}
        for r in props_raw.get("results", []):
            cls = shorten(r["domain"])
            if cls not in schema:
                schema[cls] = []
            schema[cls].append({
                "property": shorten(r["prop"]),
                "range": shorten(r.get("range", "string")),
            })

        # 4. Relaties (object properties tussen klassen)
        rels_raw = self.query(f"""
            PREFIX vakantie: <{prefix}>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?prop ?label ?domain ?range WHERE {{
                ?prop a owl:ObjectProperty ;
                      rdfs:domain ?domain ;
                      rdfs:range ?range .
                OPTIONAL {{ ?prop rdfs:label ?label }}
            }}
        """)
        relations = []
        for r in rels_raw.get("results", []):
            relations.append({
                "property": shorten(r["prop"]),
                "label": str(r.get("label", shorten(r["prop"]))),
                "from": shorten(r["domain"]),
                "to": shorten(r["range"]),
            })

        return {
            "role": role,
            "actions": list(actions.values()),
            "readonly_classes": readonly,
            "class_schema": schema,
            "relations": relations,
        }


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
        "name": "get_ontology",
        "description": "Haal de volledige OWL ontologie op als Turtle. Gebruik dit als je onzeker bent over de property namen of klassenstructuur.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "execute_action",
        "description": """Voer een ontologie-actie uit. Het systeem genereert automatisch de juiste SPARQL.
        Gebruik dit voor ALLE schrijfacties (aanmaken van entiteiten).

        Voorbeeld — klant aanmaken:
          action_type: "MaakKlant"
          params: {"naam": "Anna de Vries", "email": "anna@example.nl"}

        Voorbeeld — boeking aanmaken (met verwijzingen naar bestaande entiteiten):
          action_type: "MaakBoeking"
          params: {"checkIn": "2025-07-01", "checkOut": "2025-07-15", "aantalPersonen": 2, "status": "bevestigd", "totaalprijs": 11900}
          references: {"Klant": "data:klant1", "Hotel": "data:hotel3"}

        Het systeem kent automatisch IDs toe en maakt de juiste relaties aan.
        Zoek eerst bestaande entiteiten op met sparql_select om de data:-URIs te vinden.
        """,
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "ActionType uit de ontologie (MaakBoeking, MaakKlant, MaakHotel, MaakBestemming, AnnuleerBoeking, UpdateLoyalty)"
                },
                "params": {
                    "type": "object",
                    "description": "Property-waarden voor de nieuwe entiteit, bijv. {\"naam\": \"...\", \"email\": \"...\"}"
                },
                "references": {
                    "type": "object",
                    "description": "Verwijzingen naar bestaande entiteiten per type, bijv. {\"Klant\": \"data:klant1\", \"Hotel\": \"data:hotel3\"}"
                }
            },
            "required": ["action_type", "params"]
        }
    },
    {
        "name": "sparql_update",
        "description": """Voer een vrije SPARQL UPDATE uit. Gebruik ALLEEN voor complexe updates
        (DELETE-INSERT) die niet via execute_action kunnen. Voor het aanmaken van nieuwe entiteiten:
        gebruik altijd execute_action.

        Voorbeeld DELETE-INSERT (update):
          DELETE { ?boeking vakantie:status ?oudeStatus }
          INSERT { ?boeking vakantie:status "geannuleerd" }
          WHERE  { ?boeking a vakantie:Boeking ; vakantie:status ?oudeStatus .
                   FILTER(?boeking = data:boeking4) }
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
                    "description": "Optioneel: ontologie Action Type voor validatie"
                }
            },
            "required": ["sparql"]
        }
    },
    {
        "name": "get_capabilities",
        "description": "Haal de toegestane acties op voor jouw huidige rol, afgeleid uit de ontologie. "
                       "Toont welke ActionTypes je mag gebruiken, inclusief precondities, vereiste inputs "
                       "en read-only klassen. Gebruik dit als je onzeker bent over wat je mag doen.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]


# ═══════════════════════════════════════════════════════════════
#  SYSTEM PROMPT — Generiek + dynamisch uit ontologie
#  De ontologie is de single source of truth. Het prompt vertelt
#  de agent HOE hij de ontologie moet gebruiken, niet WAT erin staat.
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT_GENERIC = """
Je bent een intelligente database-agent voor Vakantie BV.
Je communiceert met een RDF triplestore.
De OWL ontologie is de ENIGE bron van waarheid voor wat je wel en niet mag doen.

## TOOLS
- **execute_action** — gebruik dit voor ALLE schrijfacties (aanmaken van entiteiten).
  Het systeem genereert automatisch de juiste SPARQL, IDs en relaties.
  Zoek eerst bestaande entiteiten op met sparql_select om hun data:-URI te vinden.
- **sparql_select** — gebruik dit om data op te vragen.
- **sparql_update** — ALLEEN voor DELETE-INSERT updates (bijv. status wijzigen, loyalty updaten).
  NIET voor het aanmaken van nieuwe entiteiten — gebruik daarvoor execute_action.

## SPARQL SELECT
PREFIX vakantie: <https://vakantie.nl/ontology#>
PREFIX data:     <https://vakantie.nl/data#>
PREFIX xsd:      <http://www.w3.org/2001/XMLSchema#>

Multi-hop navigatie: ?klant → ?boeking → ?hotel → ?bestemming

## GEDRAGSREGELS
1. Antwoord in het Nederlands
2. Vraag NOOIT om technische identifiers (klantId, hotel_id, data:-URIs) aan de gebruiker.
   Zoek entiteiten op naam via SELECT. Het systeem kent IDs automatisch toe.
3. Verzin NOOIT waarden voor business-properties (naam, email, datum, prijs)
   die de gebruiker niet heeft opgegeven — VRAAG ernaar.
4. Als een entiteit niet bestaat en jouw rol die niet mag aanmaken
   (zie read-only klassen hieronder), leg dit uit en toon bestaande opties.
5. Controleer altijd precondities vóór een schrijfactie (bijv. beschikbare kamers).
6. Als je onzeker bent over je rechten, gebruik de get_capabilities tool.

## VALIDATIE
Het systeem valideert automatisch met twee lagen:
1. Pre-check: bestaan gerefereerde entiteiten? Mag jouw rol dit?
2. Post-check: voldoet de graph aan SHACL shapes? (rollback bij fout)
"""


def format_capabilities(caps: dict) -> str:
    """Rendert compile_capabilities() output als compacte prompt-tekst."""
    lines = [f"\n## JOUW ROL: {caps['role'].upper()}"]

    # Toegestane acties
    lines.append("\n### Toegestane acties (uit ontologie ActionTypes):")
    for a in caps["actions"]:
        desc = f"- **{a['name']}** ({a['label']})"
        if a["creates"]:
            desc += f"\n  Maakt aan: {', '.join(a['creates'])}"
        if a["modifies"]:
            desc += f"\n  Wijzigt: {', '.join(a['modifies'])}"
        if a["setsProperties"]:
            desc += f" (property: {', '.join(a['setsProperties'])})"
        if a["requires"]:
            desc += f"\n  Vereist (moet al bestaan): {', '.join(a['requires'])}"
        if a["preconditions"]:
            desc += f"\n  Precondities: {', '.join(a['preconditions'])}"
        if a["sideEffects"]:
            desc += f"\n  Bijwerkingen: {'; '.join(a['sideEffects'])}"
        lines.append(desc)

    # Read-only klassen
    if caps["readonly_classes"]:
        ro_names = [r["label"] for r in caps["readonly_classes"]]
        lines.append(f"\n### Read-only klassen (NIET aanmaken/wijzigen):")
        lines.append(f"{', '.join(ro_names)}")
        lines.append("Zoek bestaande instanties via SELECT als de gebruiker naar deze types verwijst.")

    # Klassen-schema
    if caps["class_schema"]:
        lines.append("\n### Klassen-schema (properties per klasse):")
        for cls, props in sorted(caps["class_schema"].items()):
            prop_list = ", ".join(p["property"] for p in props)
            lines.append(f"- {cls}: {prop_list}")

    # Relaties (object properties)
    if caps.get("relations"):
        lines.append("\n### Relaties (object properties — VERPLICHT bij INSERT):")
        for rel in caps["relations"]:
            lines.append(f"- {rel['from']} → {rel['property']} → {rel['to']} ({rel['label']})")
        lines.append("Bij het aanmaken van een Boeking MOET je ook de bijbehorende relatie-triples schrijven.")

    return "\n".join(lines)


def get_system_prompt(role: str, capabilities: dict) -> str:
    """Genereert system prompt dynamisch uit ontologie-capabilities."""
    return SYSTEM_PROMPT_GENERIC + format_capabilities(capabilities)


# ═══════════════════════════════════════════════════════════════
#  AGENT LOOP
# ═══════════════════════════════════════════════════════════════
class VakantieAgent:
    def __init__(self, triplestore: VakantieTriplestore):
        self.store = triplestore
        self.client = anthropic.Anthropic()
        self.history = []
        self.role = "reisagent"
        self._cached_role = None
        self._capabilities = None
    
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

            # ── Laag 1.5: Automatische verrijking (IDs + relaties) ──
            if action_type:
                sparql = self.store.enrich_sparql(sparql, action_type)

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
        
        elif tool_name == "execute_action":
            action_type = tool_input["action_type"]
            params = tool_input.get("params", {})
            references = tool_input.get("references", {})
            print(f"\n  🎯 Execute Action: {action_type}")
            print(f"     params: {params}")
            if references:
                print(f"     references: {references}")

            # Genereer SPARQL vanuit de ontologie
            gen = self.store.generate_action_sparql(action_type, params, references)
            if "error" in gen:
                print(f"  ✗ Generatie: {gen['error']}")
                return {"success": False, "error": gen["error"]}

            sparql = gen["sparql"]
            print(f"  📝 Gegenereerde SPARQL:\n{self._indent(sparql)}")

            # Pre-validatie
            pre_check = self.store.validate_sparql_update(sparql, action_type, self.role)
            if not pre_check["valid"]:
                print(f"  ✗ Pre-validatie: {pre_check['reason']}")
                return {"success": False, "error": pre_check["reason"]}

            # Snapshot + uitvoeren
            snapshot = self.store.snapshot()
            result = self.store.update(sparql)
            if not result["success"]:
                print(f"  ✗ SPARQL fout: {result.get('error','')}")
                return result

            # SHACL post-validatie
            shacl_check = self.store.validate_graph_shacl()
            if not shacl_check["valid"]:
                print(f"  ✗ SHACL validatie gefaald — ROLLBACK")
                for v in shacl_check.get("violations", []):
                    print(f"    • {v}")
                self.store.restore(snapshot)
                return {"success": False, "error": shacl_check["reason"]}

            print(f"  ✓ {action_type} uitgevoerd → {gen['uri']} ({result['triples_total']} triples)")
            return {"success": True, "uri": gen["uri"], "triples_total": result["triples_total"]}

        elif tool_name == "get_ontology":
            print(f"\n  📖 Ontologie opgevraagd")
            return {"ontology": ONTOLOGY_TTL}

        elif tool_name == "get_capabilities":
            self._ensure_capabilities()
            print(f"\n  📋 Capabilities opgevraagd voor rol '{self.role}'")
            return self._capabilities

        return {"success": False, "error": f"Onbekende tool: {tool_name}"}
    
    def _indent(self, text: str, prefix: str = "    ") -> str:
        return "\n".join(prefix + line for line in text.strip().split("\n"))
    
    def _ensure_capabilities(self):
        """Compile capabilities bij rol-wissel of eerste aanroep."""
        if self._cached_role != self.role:
            self._capabilities = self.store.compile_capabilities(self.role)
            self._cached_role = self.role
            print(f"  📋 Capabilities gecompileerd voor rol '{self.role}': "
                  f"{len(self._capabilities['actions'])} acties, "
                  f"{len(self._capabilities['readonly_classes'])} read-only klassen")

    def chat(self, user_message: str, role: str = None) -> str:
        """Verwerk een gebruikersbericht via de agent loop."""
        if role:
            self.role = role
        self._ensure_capabilities()

        print(f"\n{'═'*60}")
        print(f"👤 [{self.role.upper()}] Gebruiker: {user_message}")
        print(f"{'═'*60}")

        self.history.append({"role": "user", "content": user_message})

        while True:
            # Retry bij overloaded/rate-limit errors
            import time
            for attempt in range(6):
                try:
                    response = self.client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=2000,
                        system=get_system_prompt(self.role, self._capabilities),
                        tools=AGENT_TOOLS,
                        messages=self.history,
                    )
                    break
                except anthropic.APIStatusError as e:
                    if attempt < 5:
                        wait = min(2 ** attempt, 30)
                        print(f"  ⏳ API error {e.status_code}, retry in {wait}s... (poging {attempt+1}/6)")
                        time.sleep(wait)
                    else:
                        raise
            
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
        role: str = "reisagent"
        reset: bool = False

    class SparqlRequest(BaseModel):
        query: str

    @app.post("/chat")
    async def chat_endpoint(req: ChatRequest):
        if req.reset:
            agent.history = []
        try:
            response = agent.chat(req.message, role=req.role)
            return {"response": response, "role": req.role, "history_length": len(agent.history)}
        except anthropic.APIStatusError:
            return {"response": "De AI-service is momenteel overbelast. Probeer het over een paar seconden opnieuw.", "role": req.role, "history_length": len(agent.history)}
        except anthropic.APIError as e:
            return {"response": f"API fout: {e.message}. Probeer het opnieuw.", "role": req.role, "history_length": len(agent.history)}
    
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
            SELECT ?hotel_id ?naam ?sterren ?prijs ?kamers ?bestemming WHERE {
                ?h a vakantie:Hotel ;
                   vakantie:hotelId ?hotel_id ;
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
            SELECT ?klant_id ?klant ?hotel_id ?hotel ?check_in ?check_out ?personen ?status ?totaalprijs WHERE {
                ?b a vakantie:Boeking ;
                   vakantie:checkIn ?check_in ;
                   vakantie:checkOut ?check_out ;
                   vakantie:aantalPersonen ?personen ;
                   vakantie:status ?status ;
                   vakantie:totaalprijs ?totaalprijs ;
                   vakantie:isGeboektIn ?h .
                ?h vakantie:naam ?hotel ;
                   vakantie:hotelId ?hotel_id .
                ?k vakantie:heeftBoeking ?b ;
                   vakantie:klantId ?klant_id ;
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

    @app.get("/ontology/meta")
    async def ontology_meta():
        """Levert ontologie-metadata als JSON voor de frontend: klassen, relaties en tools."""
        classes_result = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?cls ?label ?super ?table ?pk WHERE {
                ?cls a owl:Class ;
                     rdfs:label ?label ;
                     rdfs:subClassOf ?super ;
                     vakantie:mapsTo ?table ;
                     vakantie:primaryKey ?pk .
            } ORDER BY ?table
        """)
        classes = []
        for row in classes_result.get("results", []):
            cls_uri = str(row["cls"])
            prefix = "https://vakantie.nl/ontology#"
            cls_id = f"vakantie:{cls_uri.replace(prefix, '')}" if prefix in cls_uri else cls_uri
            super_uri = str(row["super"])
            super_map = {
                "http://xmlns.com/foaf/0.1/Person": "foaf:Person",
                "https://schema.org/LodgingBusiness": "schema:LodgingBusiness",
                "https://schema.org/Place": "schema:Place",
                "https://schema.org/Reservation": "schema:Reservation",
            }
            classes.append({
                "id": cls_id,
                "label": str(row["label"]),
                "super": super_map.get(super_uri, super_uri),
                "table": str(row["table"]),
                "pk": str(row["pk"]),
            })

        rels_result = store.query("""
            PREFIX vakantie: <https://vakantie.nl/ontology#>
            PREFIX owl: <http://www.w3.org/2002/07/owl#>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            SELECT ?prop ?domain ?range ?label WHERE {
                ?prop a owl:ObjectProperty ;
                      rdfs:domain ?domain ;
                      rdfs:range ?range .
                OPTIONAL { ?prop rdfs:label ?label }
            }
        """)
        prefix = "https://vakantie.nl/ontology#"
        prefix_short = "vakantie:"
        relationships = []
        for row in rels_result.get("results", []):
            prop_uri = str(row["prop"])
            prop_name = prop_uri.replace(prefix, "").replace(prefix_short, "")
            from_uri = str(row["domain"])
            to_uri = str(row["range"])
            relationships.append({
                "from": f"vakantie:{from_uri.replace(prefix, '')}" if prefix in from_uri else from_uri,
                "to": f"vakantie:{to_uri.replace(prefix, '')}" if prefix in to_uri else to_uri,
                "label": prop_name,
            })

        tools = [{
            "name": t["name"],
            "description": t["description"].strip(),
            "input_schema": t["input_schema"],
        } for t in AGENT_TOOLS]

        return {"classes": classes, "relationships": relationships, "tools": tools}

    @app.get("/capabilities/{role}")
    async def capabilities_endpoint(role: str):
        """Levert ontologie-capabilities voor een rol als JSON."""
        caps = store.compile_capabilities(role)
        return caps

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
