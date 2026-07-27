# Display Payload Semantic Contract

Schema version: `omega-display-payload-semantic-contract-v1`.

All Pydantic objects are recursively closed with strict types and unknown fields rejected. Notes, comments, arbitrary metadata, explanations, and free-text abstention messages do not exist. Abstention, exclusion, fallback, and health fields are controlled enumerations.

The remaining strings are field-specific, bounded, normalized with NFKC, format constrained, and—in the case of names and source labels—receipt-bound. A secondary normalized semantic guard rejects outcome, settlement, return, price, odds, profit/loss, and economic narrative tokens. This guard is defense in depth; the primary boundary is a positive closed schema with controlled values, not a keyword blacklist.
