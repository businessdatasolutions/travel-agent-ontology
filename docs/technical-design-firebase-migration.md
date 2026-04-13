# Technical Design Document: Firebase Migratie

**Project:** Vakantie BV — Ontology-Driven Agent  
**Versie:** 1.0  
**Datum:** 2026-04-13  
**Status:** Ontwerp  

---

## 1. Inleiding

### 1.1 Doel

Dit document beschrijft de technische migratie van de Vakantie BV agent-backend naar Firebase infrastructuur. De migratie voegt **persistence**, **authenticatie** en **cloud hosting** toe zonder de ontologie-gedreven architectuur (RDFLib, SPARQL, SHACL) aan te raken.

### 1.2 Scope

| In scope | Buiten scope |
|----------|-------------|
| Firebase Hosting voor de frontend | Wijzigingen aan de ontologie (OWL/SHACL) |
| Cloud Functions voor de Python backend | Vervanging van RDFLib door Firestore |
| Firestore voor graph persistence | Multi-user conversation history |
| Firebase Authentication met rol-systeem | Productie-schaling (>100 gebruikers) |
| Secret management voor API keys | CI/CD pipeline |

### 1.3 Definities

| Term | Betekenis |
|------|-----------|
| **Triplestore** | In-memory RDFLib Graph die de ontologie + data bevat |
| **Action Type** | Ontologie-entiteit die een toegestane actie beschrijft met rollen en precondities |
| **SHACL Shape** | W3C standaard constraint op de graph-structuur, afgedwongen na elke mutatie |
| **Custom Claim** | Firebase Auth metadata op een gebruiker (bijv. `{role: "admin"}`) |
| **Cold Start** | Eerste aanroep van een Cloud Function instance waarbij alle dependencies geladen worden |

---

## 2. Huidige Architectuur

### 2.1 Architectuurdiagram

```
┌──────────────────────────────────────────────────────┐
│  Browser                                             │
│  ┌────────────────────────────────────────────────┐  │
│  │  vakantie-agent.html (React + Babel)           │  │
│  │  ├─ Chat View    (berichten + snelkoppelingen) │  │
│  │  ├─ Database View (tabellen uit /state)        │  │
│  │  └─ Ontology View (SVG graaf + Turtle)         │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │ HTTP (localhost:8000)               │
└──────────────────┼───────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│  Python Process (FastAPI + Uvicorn)                   │
│  ┌─────────────────────────────────────────────────┐ │
│  │  FastAPI Server                                  │ │
│  │  ├─ POST /chat            → VakantieAgent.chat() │ │
│  │  ├─ GET  /state           → SPARQL queries → JSON│ │
│  │  ├─ GET  /ontology/meta   → klassen, relaties    │ │
│  │  ├─ GET  /capabilities/:r → compile_capabilities │ │
│  │  ├─ GET  /graph/turtle    → Turtle dump          │ │
│  │  ├─ GET  /health          → {status, triples}    │ │
│  │  └─ POST /sparql/*        → direct SPARQL        │ │
│  └───────────────┬─────────────────────────────────┘ │
│                  │                                    │
│  ┌───────────────▼─────────────────────────────────┐ │
│  │  VakantieAgent                                   │ │
│  │  ├─ Anthropic SDK → Claude Haiku 4.5            │ │
│  │  ├─ System prompt (dynamisch uit ontologie)      │ │
│  │  │  └─ compile_capabilities(role) → prompt       │ │
│  │  ├─ Tools: sparql_select, sparql_update,         │ │
│  │  │         get_ontology, get_capabilities        │ │
│  │  └─ Conversation history (in-memory)             │ │
│  └───────────────┬─────────────────────────────────┘ │
│                  │                                    │
│  ┌───────────────▼─────────────────────────────────┐ │
│  │  VakantieTriplestore (RDFLib Graph)              │ │
│  │  ├─ OWL Ontologie (klassen, properties, types)  │ │
│  │  ├─ SHACL Shapes (constraints)                   │ │
│  │  ├─ Data (klanten, hotels, boekingen, etc.)      │ │
│  │  ├─ Capability Compiler (SPARQL → rol-rechten)   │ │
│  │  ├─ Laag 1: Ontologie-gedreven pre-validatie     │ │
│  │  ├─ Laag 2: SHACL post-validatie + rollback      │ │
│  │  └─ Snapshot/Restore voor atomiciteit             │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ⚠️  GEEN PERSISTENCE — alles in-memory               │
│  ⚠️  GEEN AUTHENTICATIE — rol als plain string         │
└───────────────────────────────────────────────────────┘
```

### 2.2 Beperkingen huidige architectuur

1. **Geen persistence** — Alle data gaat verloren bij herstart van het Python proces. De triplestore wordt elke keer opnieuw geladen vanuit hardcoded Turtle strings.
2. **Geen authenticatie** — De rol (klant/admin) wordt als onbeveiligde string parameter meegegeven in de POST body. Elke gebruiker kan zichzelf admin maken.
3. **Lokale hosting** — De backend draait op `localhost:8000`. Niet bereikbaar buiten het lokale netwerk.
4. **Gedeelde agent state** — Eén `VakantieAgent` instance voor alle gebruikers. Conversation history wordt gedeeld.

### 2.3 Recente architectuurwijziging: Ontologie-gedreven prompts

Het system prompt wordt niet langer handmatig per rol geschreven. In plaats daarvan:

1. **`compile_capabilities(role)`** bevraagt de ontologie via SPARQL en genereert een gestructureerd overzicht (toegestane acties, read-only klassen, schema).
2. **`format_capabilities()`** rendert dit als prompt-tekst die dynamisch in het system prompt wordt geïnjecteerd.
3. **Pre-validatie** leest `vakantie:precondition` generiek uit de ontologie — geen hardcoded business logic meer.
4. **Frontend** haalt metadata (klassen, relaties, tools, Turtle) dynamisch op via `/ontology/meta` en `/graph/turtle` — geen hardcoded duplicaten meer.

Deze wijziging is **transparant voor de migratie**: de frontend communiceert nog steeds via dezelfde HTTP endpoints, en de ontologie-gedreven logica draait volledig server-side.

---

## 3. Doelarchitectuur

### 3.1 Architectuurdiagram

```
┌──────────────────────────────────────────────────────┐
│  Browser                                             │
│  ┌────────────────────────────────────────────────┐  │
│  │  index.html (Firebase Hosting)                 │  │
│  │  ├─ Firebase Auth UI (login/registratie)       │  │
│  │  ├─ Chat View  (+ ID token in headers)         │  │
│  │  ├─ Database View                              │  │
│  │  └─ Ontology View                              │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │ HTTPS (/api/* rewrite)             │
└──────────────────┼───────────────────────────────────┘
                   │  (zelfde domein → geen CORS)
┌──────────────────▼───────────────────────────────────┐
│  Firebase Cloud Function (Python, 2nd gen)            │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Flask App (main.py)                             │ │
│  │  ├─ Auth middleware (verify ID token)            │ │
│  │  ├─ POST /api/chat          → agent.chat()      │ │
│  │  ├─ GET  /api/state         → SPARQL → JSON     │ │
│  │  ├─ GET  /api/ontology/meta → klassen/relaties  │ │
│  │  ├─ GET  /api/capabilities/:role → uit ontologie│ │
│  │  ├─ GET  /api/health        → status            │ │
│  │  └─ POST /api/sparql/*      → admin only        │ │
│  └───────────────┬─────────────────────────────────┘ │
│                  │                                    │
│  ┌───────────────▼──────────┐  ┌───────────────────┐ │
│  │  VakantieAgent           │  │  Firestore         │ │
│  │  ├─ Anthropic SDK        │  │  ┌───────────────┐ │ │
│  │  ├─ Dynamisch prompt uit │  │  │ graphs/main   │ │ │
│  │  │  compile_capabilities │──│  │ turtle_data   │ │ │
│  │  ├─ Capability caching   │  │  │ updated_at    │ │ │
│  │  └─ on_mutation callback │  │  │ triple_count  │ │ │
│  └───────────────┬──────────┘  │  └───────────────┘ │ │
│                  │             │                     │ │
│  ┌───────────────▼──────────┐  └───────────────────┘ │
│  │  VakantieTriplestore     │                         │
│  │  ├─ RDFLib Graph         │                         │
│  │  ├─ Capability Compiler  │                         │
│  │  ├─ Ontologie-validatie  │                         │
│  │  ├─ SHACL validatie      │                         │
│  │  └─ Snapshot/Rollback    │                         │
│  └──────────────────────────┘                         │
│                                                       │
│  Config:                                              │
│  ├─ ANTHROPIC_API_KEY (Firebase Secret)               │
│  ├─ min_instances=1 (warm houden)                     │
│  └─ concurrency=1 (thread-safety)                     │
└───────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────┐
│  Firebase Authentication                               │
│  ├─ Providers: Email/Password, Google                  │
│  ├─ Custom Claims: {role: "klant"} of {role: "admin"} │
│  └─ ID Tokens: gevalideerd server-side                 │
└───────────────────────────────────────────────────────┘
```

### 3.2 Ontwerpbeslissingen

#### 3.2.1 Graph Serialisatie Strategie

**Beslissing:** Sla de volledige RDFLib graph op als één Turtle string in één Firestore document.

**Alternatieven overwogen:**

| Strategie | Voordelen | Nadelen |
|-----------|-----------|---------|
| **Eén Turtle doc (gekozen)** | Simpel, atomisch, snel | 1 MB limiet |
| Triples als losse documenten | Onbeperkte schaling | Complex, dure reads, geen SPARQL |
| Cloud Storage (GCS) | Onbeperkte grootte | Tragere reads, geen atomiciteit |
| Firestore + Cloud Storage hybride | Best of both | Complexiteit |

**Rationale:** De graph bevat ~200-1000 triples in demo-gebruik. Geserialiseerd als Turtle is dit 15-80 KB — ruim onder de 1 MB Firestore document limiet. Bij groei voorbij 1 MB kan later gemigreerd worden naar Cloud Storage.

#### 3.2.2 Cold Start Mitigatie

**Probleem:** Cloud Function cold start + RDFLib Turtle parsing + Firestore read = 3-7 seconden.

**Oplossing (drie-laags):**

1. `min_instances=1` — Firebase houdt minimaal één instance warm
2. Module-level globals — store en agent worden éénmalig geïnitialiseerd per instance
3. `concurrency=1` — voorkomt threading issues met de niet-thread-safe RDFLib Graph

```python
# Module-level (geladen bij cold start, hergebruikt per request)
_store: VakantieTriplestore | None = None
_agent: VakantieAgent | None = None

def get_store() -> VakantieTriplestore:
    global _store
    if _store is None:
        _store = initialize_store()  # Firestore → RDFLib
    return _store

def get_agent() -> VakantieAgent:
    global _agent
    if _agent is None:
        _agent = VakantieAgent(get_store())
    return _agent
```

#### 3.2.3 Authenticatie en Autorisatie

**Huidige situatie:** Rol als plain string in POST body → onbeveiligd.

**Nieuwe situatie:**

```
Login → Firebase Auth → ID Token
  ↓
Frontend stuurt token in Authorization header
  ↓
Cloud Function: verify_id_token(token) → decoded claims
  ↓
decoded['role'] = "klant" of "admin" → doorgegeven aan agent
```

De rol wordt niet meer door de client bepaald maar door de server gelezen uit de geverifieerde custom claims.

---

## 4. Componentontwerp

### 4.1 Projectstructuur

```
database-ontology-experiment/
├── firebase.json                    # Hosting rewrites + function config
├── .firebaserc                      # Project alias
├── firestore.rules                  # Security rules
├── functions/
│   ├── main.py                      # Cloud Function entry point (Flask)
│   ├── requirements.txt             # Python dependencies
│   ├── ontology_data.py             # ONTOLOGY_TTL, DATA_TTL, SHACL_TTL, namespaces
│   ├── triplestore.py               # VakantieTriplestore + compile_capabilities()
│   ├── agent.py                     # VakantieAgent (+ capability caching, on_mutation)
│   ├── prompts.py                   # AGENT_TOOLS, SYSTEM_PROMPT_GENERIC, format_capabilities()
│   ├── firestore_persistence.py     # save_graph(), load_graph(), initialize_store()
│   └── auth.py                      # require_auth() decorator
├── public/
│   └── index.html                   # Frontend (+ Firebase Auth UI)
└── .gitignore
```

### 4.2 Module-mapping vanuit huidige code

De huidige `vakantie_rdf_backend.py` wordt opgesplitst:

| Nieuwe module | Inhoud | Wijzigingen t.o.v. migratie |
|---------------|--------|----------------------------|
| `ontology_data.py` | ONTOLOGY_TTL (incl. mapsTo/primaryKey), DATA_TTL, SHACL_TTL, namespaces | Geen — pure constanten |
| `triplestore.py` | VakantieTriplestore + compile_capabilities() + ontologie-gedreven validatie | +`on_mutation` callback hook |
| `prompts.py` | AGENT_TOOLS (4 tools), SYSTEM_PROMPT_GENERIC, format_capabilities(), get_system_prompt(role, caps) | Flask-compatibele imports |
| `agent.py` | VakantieAgent met capability caching en _ensure_capabilities() | +`on_mutation` callback |
| `main.py` | Cloud Function entry point (Flask) | Flask i.p.v. FastAPI + auth |
| `firestore_persistence.py` | save_graph(), load_graph(), initialize_store() | **Nieuw** |
| `auth.py` | require_auth() decorator | **Nieuw** |

### 4.3 Firestore Persistence

#### Document Schema

```
Collection: "graphs"
└── Document: "main"
    ├── turtle_data: string       # Volledige graph als Turtle
    ├── updated_at: timestamp     # Laatste mutatie
    └── triple_count: integer     # Monitoring
```

#### API

```python
# firestore_persistence.py

def save_graph(store: VakantieTriplestore) -> None:
    """Serialiseer de graph naar Firestore na een mutatie."""
    doc_ref = db.collection("graphs").document("main")
    doc_ref.set({
        "turtle_data": store.dump_turtle(),
        "updated_at": firestore.SERVER_TIMESTAMP,
        "triple_count": len(store.graph),
    })

def load_graph() -> str | None:
    """Laad de graph Turtle string uit Firestore."""
    doc = db.collection("graphs").document("main").get()
    if doc.exists:
        return doc.to_dict().get("turtle_data")
    return None

def initialize_store() -> VakantieTriplestore:
    """Initialiseer de triplestore: uit Firestore of defaults."""
    store = VakantieTriplestore()       # laadt ONTOLOGY_TTL + DATA_TTL
    persisted = load_graph()
    if persisted:
        # Vervang de default graph met de opgeslagen versie
        store.graph = Graph()
        store.graph.parse(data=persisted, format="turtle")
        store.graph.bind("vakantie", VAKANTIE)
        store.graph.bind("data", DATA)
    return store
```

### 4.4 Cloud Function Entry Point

```python
# main.py

from firebase_functions import https_fn
from flask import Flask, request, jsonify
from triplestore import VakantieTriplestore
from agent import VakantieAgent
from firestore_persistence import initialize_store, save_graph
from auth import require_auth

app = Flask(__name__)

# Module-level globals (hergebruikt per instance)
_store = None
_agent = None

def get_store():
    global _store
    if _store is None:
        _store = initialize_store()
    return _store

def get_agent():
    global _agent
    if _agent is None:
        agent = VakantieAgent(get_store())
        agent.on_mutation = lambda: save_graph(get_store())
        _agent = agent
    return _agent

@app.route("/api/chat", methods=["POST"])
@require_auth
def chat():
    data = request.json
    agent = get_agent()
    if data.get("reset"):
        agent.history = []
    # Rol komt uit Firebase Auth custom claims (niet uit POST body)
    response = agent.chat(data["message"], role=request.role)
    return jsonify({"response": response, "role": request.role})

@app.route("/api/state")
@require_auth
def state():
    store = get_store()
    # ... SPARQL queries (ongewijzigd uit huidige code)

@app.route("/api/capabilities/<role>")
@require_auth
def capabilities(role):
    """Ontologie-capabilities voor een rol — gebruikt door frontend ontologie-view."""
    store = get_store()
    return jsonify(store.compile_capabilities(role))

@app.route("/api/ontology/meta")
def ontology_meta():
    """Ontologie-metadata (klassen, relaties, tools) voor frontend."""
    store = get_store()
    # ... SPARQL queries voor klassen, relaties + AGENT_TOOLS

@app.route("/api/health")
def health():
    store = get_store()
    return jsonify({"status": "ok", "triples": len(store.graph)})

@https_fn.on_request(
    secrets=["ANTHROPIC_API_KEY"],
    memory=512,
    min_instances=1,
    concurrency=1,
)
def api(req: https_fn.Request) -> https_fn.Response:
    with app.request_context(req.environ):
        return app.full_dispatch_request()
```

### 4.5 Authenticatie Middleware

```python
# auth.py

import functools
import firebase_admin
from firebase_admin import auth as firebase_auth
from flask import request, jsonify

firebase_admin.initialize_app()

def require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Geen authenticatie token"}), 401
        
        token = auth_header.replace("Bearer ", "")
        try:
            decoded = firebase_auth.verify_id_token(token)
            request.user = decoded
            request.role = decoded.get("role", "klant")
        except Exception:
            return jsonify({"error": "Ongeldig token"}), 401
        
        return f(*args, **kwargs)
    return wrapper
```

### 4.6 Frontend Wijzigingen

**Huidige staat:** De frontend (`vakantie-agent.html`) haalt alle metadata dynamisch op van de backend:
- `/ontology/meta` → klassen, relaties, tools (merged met lokale UI_CONFIG voor icons/kleuren)
- `/graph/turtle` → live Turtle serialisatie voor ontologie-view
- `/state` → data voor database-view
- Geen hardcoded database, geen hardcoded ontologie-structuur

Bij de Firebase migratie verandert alleen de **base URL** (van `localhost:8000` naar `/api`) en worden **authenticatie headers** toegevoegd.

#### Firebase SDK toevoegen (in `<head>`):

```html
<script src="https://www.gstatic.com/firebasejs/10.x/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.x/firebase-auth-compat.js"></script>
```

#### Auth state management (in React App component):

```javascript
const [user, setUser] = useState(null);

useEffect(() => {
    firebase.auth().onAuthStateChanged(setUser);
}, []);

// Bij elke fetch: token meesturen
const sendWithAuth = async (url, options = {}) => {
    const token = await firebase.auth().currentUser.getIdToken();
    return fetch(url, {
        ...options,
        headers: {
            ...options.headers,
            "Authorization": `Bearer ${token}`,
            "Content-Type": "application/json",
        },
    });
};
```

#### Rol uit token lezen:

```javascript
// Na login: custom claims ophalen
const tokenResult = await user.getIdTokenResult();
const role = tokenResult.claims.role || "klant";
setAgentRole(role);
// De handmatige klant/admin toggle in de UI wordt verwijderd —
// de rol is nu bepaald door Firebase Auth custom claims.
```

#### API_BASE wijzigen:

```javascript
// Huidig (lokale ontwikkeling):
const API_BASE = "http://localhost:8000";

// Firebase Hosting (productie):
const API_BASE = "/api";
// Zelfde domein → geen CORS nodig
```

De drie opstart-fetches (`/ontology/meta`, `/graph/turtle`, `/state`) werken ongewijzigd — alleen de base URL en auth header veranderen.

---

## 5. Configuratie

### 5.1 firebase.json

```json
{
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/node_modules/**"],
    "rewrites": [
      { "source": "/api/**", "function": "api" }
    ]
  },
  "functions": {
    "source": "functions",
    "runtime": "python312"
  },
  "firestore": {
    "rules": "firestore.rules"
  }
}
```

### 5.2 Firestore Security Rules

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Graph document: alleen server-side toegang via Admin SDK
    match /graphs/{graphId} {
      allow read, write: if false;
    }
  }
}
```

### 5.3 Dependencies

```
# functions/requirements.txt
firebase-functions>=0.1.0
firebase-admin>=6.0.0
rdflib>=7.0.0
pyshacl>=0.25.0
anthropic>=0.40.0
flask>=3.0.0
```

Alle dependencies zijn pure Python. Geen native C extensions vereist. Compatibel met Cloud Functions runtime.

### 5.4 Secret Management

```bash
# Eenmalig: API key opslaan als Firebase Secret
firebase functions:secrets:set ANTHROPIC_API_KEY

# De key is beschikbaar als os.environ["ANTHROPIC_API_KEY"]
# De Anthropic SDK leest dit automatisch — geen codewijziging nodig
```

---

## 6. Component-status bij migratie

### 6.1 Ongewijzigd overnemen

| Component | Beschrijving |
|-----------|-------------|
| `ONTOLOGY_TTL` | OWL ontologie: klassen (met mapsTo/primaryKey), properties, Action Types met allowedRole, preconditions, sideEffects |
| `SHACL_TTL` | SHACL shapes voor Boeking, Hotel, Klant |
| `DATA_TTL` | Initiële dataset: 4 klanten, 4 bestemmingen, 6 hotels, 4 boekingen |
| SHACL post-validatie | pyshacl validate() met rollback bij falen |
| `/state` SPARQL queries | 4 SELECT queries die de frontend data opbouwen |

### 6.2 Gewijzigd sinds initieel ontwerp (overnemen in huidige vorm)

Deze componenten zijn gerefactored naar een **ontologie-gedreven architectuur** en moeten in hun huidige vorm overgenomen worden:

| Component | Beschrijving | Wijziging t.o.v. initieel ontwerp |
|-----------|-------------|-----------------------------------|
| `VakantieTriplestore` | RDFLib Graph met query(), update(), snapshot(), restore() | +`compile_capabilities(role)`: bevraagt ontologie via SPARQL en genereert gestructureerd overzicht van toegestane acties, read-only klassen en schema per rol. +`_get_preconditions()`, `_evaluate_precondition()`: generieke precondition-evaluatie uit ontologie |
| `validate_sparql_update()` | Ontologie-gedreven pre-validatie | Leest nu `vakantie:precondition` generiek uit de ontologie i.p.v. hardcoded `if action_type == "MaakBoeking"`. Foutmeldingen verwijzen naar ontologie-constraints (bijv. `vakantie:MaakKlant vakantie:allowedRole`) |
| `AGENT_TOOLS` | 4 tools: sparql_select, sparql_update, get_ontology, **get_capabilities** | +`get_capabilities`: agent kan ontologie-rechten opvragen als escape hatch |
| `SYSTEM_PROMPT_GENERIC` | Minimaal generiek prompt | Vervangt de statische `SYSTEM_PROMPT_BASE/KLANT/ADMIN`. Bevat alleen HOE de agent de ontologie moet gebruiken, niet WAT erin staat |
| `format_capabilities()` | Rendert compile_capabilities() output als prompt-tekst | **Nieuw** — genereert rol-specifiek prompt-blok dynamisch uit ontologie |
| `get_system_prompt(role, caps)` | Combineert generiek prompt + capabilities | Signature gewijzigd: neemt nu capabilities dict als parameter |
| `VakantieAgent` | Agent loop met Anthropic SDK | +`_ensure_capabilities()`: cached capabilities per rol. +`get_capabilities` tool handler |

---

## 7. Risico's en Mitigatie

| # | Risico | Kans | Impact | Mitigatie |
|---|--------|------|--------|-----------|
| 1 | Cold start latency (3-7s) | Hoog bij lage traffic | Slechte gebruikerservaring | `min_instances=1` (kost ~$10/maand) |
| 2 | Firestore document >1MB | Laag (demo-schaal) | Graph niet opslaanbaar | Migreer naar Cloud Storage bij groei |
| 3 | Concurrent mutaties | Middel | Data inconsistentie | `concurrency=1` per instance |
| 4 | Conversation history verloren | Hoog (bij instance recycle) | Context verlies | Acceptabel voor demo; later Firestore |
| 5 | PyShacl/RDFLib incompatibiliteit | Laag | Deploy faalt | Pure Python; getest met Cloud Functions |
| 6 | Firebase Auth token expiratie | Middel | 401 errors | Frontend auto-refresh via `getIdToken(true)` |

---

## 8. Verificatieplan

| # | Test | Verwacht resultaat |
|---|------|--------------------|
| 1 | `firebase emulators:start` | Alle services draaien lokaal |
| 2 | POST `/api/chat` zonder token | 401 Unauthorized |
| 3 | Login via Firebase Auth UI | Token ontvangen, chat werkt |
| 4 | Chat: "Toon alle klanten" (rol: klant) | SPARQL SELECT succesvol |
| 5 | Chat: "Maak klant Witek aan" (rol: klant) | Agent weigert (ontologie: readOnly + niet in toegestane acties). Pre-validatie blokkeert met ontologie-referentie |
| 6 | Chat: "Maak klant Witek aan" (rol: admin) | Klant aangemaakt, SHACL valide |
| 7 | GET `/api/capabilities/klant` | JSON met 3 acties (MaakBoeking, AnnuleerBoeking, UpdateLoyalty) + 3 read-only klassen |
| 8 | GET `/api/capabilities/admin` | JSON met 6 acties (incl. MaakKlant, MaakHotel, MaakBestemming) |
| 9 | GET `/api/ontology/meta` | Klassen, relaties en tools uit ontologie (frontend metadata) |
| 10 | Voeg nieuw ActionType toe aan ontologie | Agent ontdekt het automatisch zonder code-wijziging |
| 11 | Herstart emulator, GET `/api/state` | Data nog aanwezig (Firestore) |
| 12 | `firebase deploy`, test op live URL | Alles werkt op productie |

---

## 9. Deployment

```bash
# 1. Firebase project aanmaken
firebase init   # Functions (Python), Hosting, Firestore, Auth

# 2. API key als secret
firebase functions:secrets:set ANTHROPIC_API_KEY

# 3. Eerste admin aanmaken (eenmalig, via script)
python -c "
import firebase_admin
from firebase_admin import auth
firebase_admin.initialize_app()
auth.set_custom_user_claims('<admin-uid>', {'role': 'admin'})
"

# 4. Deploy
firebase deploy

# 5. Verify
curl https://<project-id>.web.app/api/health
```
