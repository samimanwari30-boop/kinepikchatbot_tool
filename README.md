# KINEPIK Chatbot

A conversational interface to KINEPIK that lets you explore kinase signalling using plain English instead of writing API calls or digging through raw data.

**Live site: https://kinepikchatbot.com**

## What it does

Ask a question about a kinase, a drug or a signalling network and the chatbot works out what you actually need, runs the right analysis and gives you a proper answer along with a chart or table where it makes sense.

It can do things like:

- Find which kinases are most activated or inhibited under a given drug and cell line, or look up a single kinase directly
- Compare a kinase's activity across different drugs or cell lines, or build a heatmap across several kinases at once
- Show you a kinase's signalling network or check whether it targets a specific substrate
- Pull up a searchable table of every phosphosite a kinase is known to act on
- Answer follow up questions properly, so if you ask "does the top one target BRCA1" it knows which kinase you mean

## Where the data comes from

Everything is served from KINEPIK (kinepik.org), a database built from phosphoproteomics measurements and kinase substrate interaction data. It covers 504 kinases across 61 drug perturbations in 3 cell lines and draws on published sources including LINCS P100 assay data, KINOMEscan binding assays and cell line specific experimental datasets.

## How it works

The chatbot is a Flask app that talks to a separate internal API (pikapi) which handles the actual database queries. When you ask a question, GPT picks the right tool for the job, the tool runs the analysis against the real data and GPT writes a plain English answer based on what came back. It doesn't make anything up, it only reports what the tool actually returned.

## Running it locally

You'll need Python 3 and the packages listed in `requirements.txt`, plus a running pikapi instance and an OpenAI API key set as an environment variable.

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=your-key-here
python app.py
```

The app runs on port 5001 by default and expects pikapi to be reachable at `127.0.0.1:5002`.

## Deployment

The live site runs on an AWS Lightsail instance using Gunicorn behind nginx, with HTTPS through Let's Encrypt. pikapi is kept internal and is never exposed to the public internet, only the chatbot is.
