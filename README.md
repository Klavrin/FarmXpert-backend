# FarmXpert Backend

This is the python backend service for FarmXpert a web platform that digitalizes the agricultural subsidy process in Moldova.  
It provides **REST APIs** and **AI-powered features** to help farmers and government agencies manage subsidy applications, automate document handling, and ensure compliance with AIPA regulations.

For the Blazor frontend web app of FarmXpert, access the repository here: 
```bash
https://github.com/Klavrin/FarmXpert
```

---

## Features

- **Subsidy Matching API** – AI-assisted recommendations and ranking of subsidies for a given farm profile.
- **Document Automation** – Auto-fill and validate subsidy application forms.
- **Farm & User Management** – Manage farm profiles, land parcels, and user data.
- **Integration Ready** – Designed to connect with external government data sources (e.g., `data2b.md`).

---

## How to start

### 1. Clone and install dependencies

```bash
git clone https://github.com/Klavrin/FarmXpert-backend.git
cd farmxpert-backend
pip install -r requirements.txt
```

## 2. Set environment variables

```bash
DATABASE_URL="your_mongodb_url"
OPENAI_API_KEY="your_openai_api_key"
GPT_MODEL="gpt-5-nano"
```

## 3. Run the server

```python
flask run
```

