# Display Row Identity Contract

Schema version: `omega-display-row-identity-contract-v1`.

Every row, including an abstention, requires a positive official `game_pk`, MLB batter ID, MLB team ID, MLB opponent ID, `RESOLVED` state, source receipt SHA-256, chronology receipt SHA-256, identity receipt SHA-256, schema version, and content SHA-256. The row's receipt hashes must resolve to manifest-bound receipt objects whose player, game, team, opponent, canonical display name, source, and chronology fields match the row exactly.

An opposing-starter block additionally requires the resolved starter's official MLB ID and its own identity receipt. Names are display values only and never establish identity. Any mismatch rejects the complete snapshot.
