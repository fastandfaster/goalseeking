# How to Get Your Team Data Into the Optimizer

## Step-by-Step Instructions

### Step 1: Log into ALTA
1. Go to https://www.altatennis.org/Member/Dashboard.aspx
2. Log in with your ALTA credentials

### Step 2: Find Your Team
1. Click on your **Sunday Women C-8** team
2. Go to your **Team Schedule / Match Results** page

### Step 3: Copy Your Data
For each of the 7 regular season matches (Mar 16 – Apr 27):
- Select ALL the text on the match results page
- Copy it (Ctrl+A, Ctrl+C)
- Paste it into our next chat session

**What I need for each match:**
```
Match Date: 2026-03-16
Opponent: [team name]

Line 1: [Player Name] & [Partner Name] — Won/Lost [score]
Line 2: [Player Name] & [Partner Name] — Won/Lost [score]  
Line 3: [Player Name] & [Partner Name] — Won/Lost [score]
Line 4: [Player Name] & [Partner Name] — Won/Lost [score]
Line 5: [Player Name] & [Partner Name] — Won/Lost [score]
```

### Step 4: Player Availability
Tell me which players are NOT available for:
- May 3 (first playoff round)
- May 10 (if you advance)
- May 18 (C-Flight City Finals)

### Step 5: Captain Preferences (Optional)
- Any pairs you WANT to keep together?
- Any pairs you want to AVOID?
- Aggressive or conservative strategy?

## Alternative: Quick Roster Method
If copy/pasting match results is too tedious, just give me:

1. **Full roster** (all player names)
2. **For each player**: which line(s) they usually played, approximate W/L record
3. **Who's available** for playoffs

I can work with approximate data — it's better than sample data!

## Then Run
```bash
cd C:\Users\hongyang\AI\goalseeking
python lineup_optimizer.py sample_data.json
```
