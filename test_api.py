# This file is used for manually testing KINEPIK API calls during development.
# Uncomment the relevant lines and run this file directly to inspect raw API responses.

# Example: look up a protein by UniProt ID with a specific field filter
#import requests
#r1 = requests.get("https://kinepik.org/api/0/proteins/results?protein_ids=P31749&fields=kinase")
#print("=== fields=kinase ===")
#print(r1.json())

#print()

# Example: look up a protein with the mappedgene field
#r2 = requests.get("https://kinepik.org/api/0/proteins/results?protein_ids=P31749&fields=mappedgene")
#print("=== fields=mappedgene ===")
#print(r2.json())

# Example: pretty-print any result as formatted JSON
#import json
#print(json.dumps(result, indent=2))
