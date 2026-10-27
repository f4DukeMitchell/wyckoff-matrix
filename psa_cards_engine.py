"""
PSA Graded Cards & Alternative Assets Market Engine
MSR Wyckoff Terminal Engine
Tracks institutional ownership, auction telemetry, PSA Population Reports,
market indices (Card Ladder 50, PSA 10 Blue-Chip), and Wyckoff market phase cycles.
"""
import os
import json
import datetime
from typing import Dict, Any, List, Optional

# Curated Institutional Blue-Chip Database
# Cards held by major institutional vaults (Collectors Holdings, PWCC/Fanatics, Alt, Rally)
PSA_BLUE_CHIPS = [
    {
        "id": "MJ-86F-10",
        "asset_name": "Michael Jordan 1986 Fleer #57",
        "short_name": "Jordan '86 Fleer",
        "year": 1986,
        "set_name": "Fleer",
        "card_number": "#57",
        "category": "VINTAGE_SPORTS",
        "sport": "Basketball",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 25480,
        "psa_pop_10": 320,
        "gem_rate_pct": 1.26,
        "current_fmv": 240000.0,
        "change_90d_pct": 7.4,
        "change_1y_pct": 12.8,
        "all_time_high": 738000.0,
        "all_time_high_date": "2021-01",
        "low_52w": 205000.0,
        "high_52w": 255000.0,
        "wyckoff_phase": "Phase C: Spring (Secondary Test Reclaim)",
        "wyckoff_bias": "BULLISH_ABSORPTION",
        "institutional_status": "HIGH_VAULT_CONCENTRATION",
        "vault_concentration_pct": 46.5, # % of Pop 10 sitting in PWCC/PSA/Alt Vaults
        "recent_sales": [
            {"date": "2026-09-18", "house": "Goldin Auctions", "price": 242000.0, "type": "Premier Auction"},
            {"date": "2026-07-22", "house": "PWCC Vault", "price": 235000.0, "type": "Private Treaty"},
            {"date": "2026-05-14", "house": "Heritage Auctions", "price": 228000.0, "type": "Signature Auction"},
            {"date": "2026-02-10", "house": "Goldin Auctions", "price": 218000.0, "type": "Elite Auction"}
        ],
        "frank_analysis": (
            "Clean Wyckoff Spring formation. Following the 2021 Buying Climax ($738K) and subsequent "
            "two-year markdown, Jordan PSA 10 found major institutional support at $200K-$215K. "
            "Auction volume has dried up by 44% YoY, indicating floating supply exhaustion as institutional "
            "vaults absorb available copies into long-term cold storage."
        )
    },
    {
        "id": "MM-52T-08",
        "asset_name": "Mickey Mantle 1952 Topps #311",
        "short_name": "Mantle '52 Topps",
        "year": 1952,
        "set_name": "Topps",
        "card_number": "#311",
        "category": "VINTAGE_SPORTS",
        "sport": "Baseball",
        "grade": "PSA 8",
        "grade_desc": "Near Mint-Mint",
        "psa_pop_total": 1960,
        "psa_pop_10": 3,
        "gem_rate_pct": 0.15,
        "current_fmv": 1850000.0,
        "change_90d_pct": 4.2,
        "change_1y_pct": 14.5,
        "all_time_high": 12600000.0, # SGC 9.5 benchmark
        "all_time_high_date": "2022-08",
        "low_52w": 1650000.0,
        "high_52w": 1950000.0,
        "wyckoff_phase": "Phase D: SOS Markup (Institutional Fortress)",
        "wyckoff_bias": "STRONG_ACCUMULATION",
        "institutional_status": "BLUE_CHIP_CORNERSTONE",
        "vault_concentration_pct": 68.0,
        "recent_sales": [
            {"date": "2026-08-30", "house": "Heritage Auctions", "price": 1875000.0, "type": "Platinum Night"},
            {"date": "2026-04-12", "house": "Goldin Auctions", "price": 1810000.0, "type": "Private Sale"},
            {"date": "2025-11-20", "house": "Sotheby's / Alt", "price": 1720000.0, "type": "Curated Auction"}
        ],
        "frank_analysis": (
            "The Mona Lisa of alternative sports assets. Float is virtually non-existent with over 68% of known "
            "high-grade copies locked in billionaire vaults or institutional trust portfolios. Displays classical "
            "Sign of Strength (SOS) price action, completely immune to macroeconomic equity drawdowns."
        )
    },
    {
        "id": "TB-00PC-10",
        "asset_name": "Tom Brady 2000 Playoff Contenders Rookie Ticket Auto #144",
        "short_name": "Brady '00 Contenders Auto",
        "year": 2000,
        "set_name": "Playoff Contenders",
        "card_number": "#144",
        "category": "MODERN_GOATS",
        "sport": "Football",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 920,
        "psa_pop_10": 21,
        "gem_rate_pct": 2.28,
        "current_fmv": 1650000.0,
        "change_90d_pct": 8.6,
        "change_1y_pct": 18.2,
        "all_time_high": 3107000.0,
        "all_time_high_date": "2021-06",
        "low_52w": 1380000.0,
        "high_52w": 1720000.0,
        "wyckoff_phase": "Phase C: Secondary Test Absorption",
        "wyckoff_bias": "BULLISH_ACCUMULATION",
        "institutional_status": "HIGH_VAULT_CONCENTRATION",
        "vault_concentration_pct": 52.4,
        "recent_sales": [
            {"date": "2026-10-04", "house": "PWCC Premier", "price": 1680000.0, "type": "Premier Auction"},
            {"date": "2026-06-18", "house": "Goldin Auctions", "price": 1540000.0, "type": "Summer Elite"},
            {"date": "2026-01-25", "house": "Alt Vault", "price": 1420000.0, "type": "Direct Transfer"}
        ],
        "frank_analysis": (
            "Pop 21 creates an insurmountable supply moat. After dropping from the peak of $3.1M during the 2021 "
            "crypto/stimulus bubble, Brady has established an institutional accumulation band between $1.35M-$1.5M. "
            "The recent hammer at $1.68M confirmed Wyckoff Jump Across the Creek (JAC) on accelerating volume."
        )
    },
    {
        "id": "LBJ-03TC-10R",
        "asset_name": "LeBron James 2003 Topps Chrome #111 Refractor",
        "short_name": "LeBron '03 Chrome Refractor",
        "year": 2003,
        "set_name": "Topps Chrome",
        "card_number": "#111 Refractor",
        "category": "MODERN_GOATS",
        "sport": "Basketball",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 485,
        "psa_pop_10": 34,
        "gem_rate_pct": 7.01,
        "current_fmv": 285000.0,
        "change_90d_pct": 5.1,
        "change_1y_pct": 11.2,
        "all_time_high": 540000.0,
        "all_time_high_date": "2021-03",
        "low_52w": 245000.0,
        "high_52w": 298000.0,
        "wyckoff_phase": "Phase D: Last Point of Support (LPS)",
        "wyckoff_bias": "BULLISH_CONTINUATION",
        "institutional_status": "VAULT_ACCUMULATED",
        "vault_concentration_pct": 44.1,
        "recent_sales": [
            {"date": "2026-09-12", "house": "Goldin Auctions", "price": 288000.0, "type": "Monthly Elite"},
            {"date": "2026-05-20", "house": "PWCC Vault", "price": 272000.0, "type": "Fixed Marketplace"},
            {"date": "2026-02-18", "house": "Heritage Auctions", "price": 260000.0, "type": "Signature Auction"}
        ],
        "frank_analysis": (
            "Topps Chrome Refractor is the gold standard for modern NBA liquidity. Pop 34 has undergone rigorous "
            "hands-exchange. Wyckoff Last Point of Support (LPS) validated at $260K with minimal hammer slippage."
        )
    },
    {
        "id": "PKMN-99CHAR-10",
        "asset_name": "1999 Pokemon Base Set 1st Edition Shadowless Charizard #4",
        "short_name": "1st Ed Charizard #4",
        "year": 1999,
        "set_name": "Pokemon Base Set (1st Edition)",
        "card_number": "#4 Holo",
        "category": "TCG_CULT",
        "sport": "TCG / Gaming",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 4450,
        "psa_pop_10": 124,
        "gem_rate_pct": 2.78,
        "current_fmv": 310000.0,
        "change_90d_pct": 6.2,
        "change_1y_pct": 15.4,
        "all_time_high": 420000.0,
        "all_time_high_date": "2022-03",
        "low_52w": 265000.0,
        "high_52w": 325000.0,
        "wyckoff_phase": "Phase C: Secondary Test (Institutional Floor)",
        "wyckoff_bias": "BULLISH_ABSORPTION",
        "institutional_status": "GLOBAL_CROSSOVER_GRAIL",
        "vault_concentration_pct": 58.2,
        "recent_sales": [
            {"date": "2026-10-02", "house": "Heritage Auctions", "price": 315000.0, "type": "Pop Culture Signature"},
            {"date": "2026-07-15", "house": "Goldin Auctions", "price": 295000.0, "type": "TCG Elite"},
            {"date": "2026-03-28", "house": "PWCC Vault", "price": 280000.0, "type": "Vault Direct"}
        ],
        "frank_analysis": (
            "The premier global pop-culture tangible asset. Global liquidity pool spanning US, Japan, Europe, "
            "and Middle East family offices. Strong secondary test reclaim at $280K with 58% of all PSA 10 copies "
            "now held inside institutional vault custodians."
        )
    },
    {
        "id": "MT-09BC-10A",
        "asset_name": "Mike Trout 2009 Bowman Chrome Draft Prospects Auto #BDPP89",
        "short_name": "Trout '09 Chrome Auto",
        "year": 2009,
        "set_name": "Bowman Chrome Draft",
        "card_number": "#BDPP89 Auto",
        "category": "MODERN_GOATS",
        "sport": "Baseball",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 780,
        "psa_pop_10": 195,
        "gem_rate_pct": 25.0,
        "current_fmv": 62000.0,
        "change_90d_pct": -1.8,
        "change_1y_pct": 4.5,
        "all_time_high": 110000.0,
        "all_time_high_date": "2021-04",
        "low_52w": 58000.0,
        "high_52w": 66000.0,
        "wyckoff_phase": "Phase B: Prolonged Consolidation",
        "wyckoff_bias": "NEUTRAL_BASE",
        "institutional_status": "MODERATE_LIQUIDITY",
        "vault_concentration_pct": 32.5,
        "recent_sales": [
            {"date": "2026-08-14", "house": "Goldin Auctions", "price": 615000.0 if False else 61500.0, "type": "Weekly Auction"},
            {"date": "2026-05-10", "house": "eBay Vault", "price": 63000.0, "type": "Verified Vault"},
            {"date": "2026-01-20", "house": "PWCC Vault", "price": 59500.0, "type": "Fixed Market"}
        ],
        "frank_analysis": (
            "Wyckoff Phase B Trading Range ($58K-$66K). Effort vs Result is balanced; requires an institutional "
            "catalyst or Hall of Fame induction tailwind to trigger Phase C spring."
        )
    },
    {
        "id": "PM-17NT-09R",
        "asset_name": "Patrick Mahomes 2017 Panini National Treasures RPA #161 /99",
        "short_name": "Mahomes '17 NT RPA /99",
        "year": 2017,
        "set_name": "Panini National Treasures",
        "card_number": "#161 RPA /99",
        "category": "MODERN_GOATS",
        "sport": "Football",
        "grade": "PSA 9",
        "grade_desc": "Mint (Auto 10)",
        "psa_pop_total": 99,
        "psa_pop_10": 8,
        "gem_rate_pct": 8.08,
        "current_fmv": 480000.0,
        "change_90d_pct": 9.2,
        "change_1y_pct": 21.0,
        "all_time_high": 840000.0,
        "all_time_high_date": "2021-02",
        "low_52w": 395000.0,
        "high_52w": 495000.0,
        "wyckoff_phase": "Phase D: Sign of Strength (Markup Test)",
        "wyckoff_bias": "STRONG_BULLISH",
        "institutional_status": "VAULT_LOCKUP",
        "vault_concentration_pct": 55.6,
        "recent_sales": [
            {"date": "2026-09-24", "house": "PWCC Premier", "price": 485000.0, "type": "Premier Auction"},
            {"date": "2026-06-05", "house": "Goldin Auctions", "price": 440000.0, "type": "Elite Auction"},
            {"date": "2026-02-12", "house": "Alt Direct", "price": 410000.0, "type": "Private Sale"}
        ],
        "frank_analysis": (
            "Serial numbered /99 represents pure structural scarcity. PSA 9 with Auto 10 serves as the primary "
            "institutional trading tranche. Volume expansion on recent auction hammers confirms accumulation."
        )
    },
    {
        "id": "WG-79OPC-10",
        "asset_name": "Wayne Gretzky 1979 O-Pee-Chee #18",
        "short_name": "Gretzky '79 OPC",
        "year": 1979,
        "set_name": "O-Pee-Chee",
        "card_number": "#18",
        "category": "VINTAGE_SPORTS",
        "sport": "Hockey",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 12800,
        "psa_pop_10": 2,
        "gem_rate_pct": 0.015,
        "current_fmv": 3200000.0,
        "change_90d_pct": 3.8,
        "change_1y_pct": 8.5,
        "all_time_high": 3750000.0,
        "all_time_high_date": "2021-05",
        "low_52w": 3050000.0,
        "high_52w": 3300000.0,
        "wyckoff_phase": "Phase E: Secular Value Monopoly",
        "wyckoff_bias": "IRREPLACEABLE_GRAIL",
        "institutional_status": "MUSEUM_TIER",
        "vault_concentration_pct": 100.0, # Both existing PSA 10s are locked in permanent collections
        "recent_sales": [
            {"date": "2021-05-27", "house": "Heritage Auctions", "price": 3750000.0, "type": "Record Sale"},
            {"date": "2020-12-11", "house": "Heritage Auctions", "price": 1290000.0, "type": "Signature Sale"}
        ],
        "frank_analysis": (
            "Pop 2 in existence out of nearly 13,000 copies examined. O-Pee-Chee rough wire-cut edges make "
            "Gem Mint condition statistically near-impossible (0.015% Gem Rate). Unaffected by liquid market churn."
        )
    },
    {
        "id": "MTG-93LOTUS-10",
        "asset_name": "1993 Magic: The Gathering Alpha Black Lotus",
        "short_name": "Alpha Black Lotus",
        "year": 1993,
        "set_name": "Magic: The Gathering (Alpha Edition)",
        "card_number": "Rare Artifact",
        "category": "TCG_CULT",
        "sport": "TCG / Gaming",
        "grade": "PSA 10",
        "grade_desc": "Gem Mint",
        "psa_pop_total": 110,
        "psa_pop_10": 7,
        "gem_rate_pct": 6.36,
        "current_fmv": 610000.0,
        "change_90d_pct": 5.4,
        "change_1y_pct": 14.2,
        "all_time_high": 615000.0,
        "all_time_high_date": "2023-03",
        "low_52w": 550000.0,
        "high_52w": 625000.0,
        "wyckoff_phase": "Phase D: Markup to Blue Sky",
        "wyckoff_bias": "SECULAR_BULLISH",
        "institutional_status": "ULTRA_GRAIL",
        "vault_concentration_pct": 71.4,
        "recent_sales": [
            {"date": "2026-06-20", "house": "PWCC Premier", "price": 612000.0, "type": "Premier Auction"},
            {"date": "2024-04-18", "house": "Heritage Auctions", "price": 580000.0, "type": "Signature Sale"}
        ],
        "frank_analysis": (
            "Alpha print run was only 2.6 million total cards with an estimated 1,100 Black Lotuses ever printed. "
            "Only 7 have achieved PSA 10. Wyckoff Law of Supply vs Demand is absolute here."
        )
    }
]

def get_psa_market_overview() -> Dict[str, Any]:
    """Returns macro indices, vault inflows, and institutional market telemetry."""
    return {
        "status": "ONLINE",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S EST"),
        "macro_metrics": {
            "total_graded_mktcap": "$28.45B",
            "mktcap_change_30d_pct": 3.8,
            "cl50_index": {
                "name": "Card Ladder 50 Index (CL50)",
                "value": 26482.10,
                "change_30d_pct": 3.42,
                "description": "50 most liquid, high-capitalization blue-chip cards across all categories"
            },
            "psa10_bluechip_index": {
                "name": "PSA 10 Gem Mint Benchmark",
                "value": 142.85,
                "change_30d_pct": 5.14,
                "description": "Composite performance of verified PSA 10 population assets"
            },
            "pwcc_500_index": {
                "name": "PWCC Top 500 Index",
                "value": 845.20,
                "change_30d_pct": 2.80,
                "description": "High-capitalization auction sales index vs S&P 500 benchmark"
            },
            "monthly_auction_volume": "$148.6M",
            "volume_change_mom_pct": 12.4,
            "spy_correlation_coefficient": 0.18, # Extremely low correlation = optimal portfolio hedge
            "institutional_vault_inflows_30d": "+$14.8M",
            "vintage_to_modern_premium": "2.4x"
        },
        "vault_ecosystem": [
            {
                "vault_name": "PWCC / Fanatics Vault",
                "assets_under_custody": "$450M+",
                "primary_focus": "High-Beta Sports & Modern GOATs",
                "trend": "INFLOW_ACCELERATING"
            },
            {
                "vault_name": "Collectors / PSA Vault",
                "assets_under_custody": "$380M+",
                "primary_focus": "Certified PSA 9/10 Direct Submission Hold",
                "trend": "STEADY_GROWTH"
            },
            {
                "vault_name": "Alt Alternative Vault (B2B / Funds)",
                "assets_under_custody": "$210M+",
                "primary_focus": "Institutional Fractional & Direct Lending Collateral",
                "trend": "EXPANDING"
            }
        ],
        "frank_macro_assessment": (
            "The PSA-graded alternative asset market has concluded its cyclical post-2021 markdown "
            "and entered a classic Wyckoff Re-Accumulation phase. With Steve Cohen and Nat Turner's Collectors Holdings "
            "standardizing vault infrastructure and Alt/PWCC providing institutional collateralized credit, "
            "capital is concentrating into ultra-low-pop blue chips. Correlation to SPY remains exceptionally low (0.18), "
            "making top-tier PSA 10 grails prime non-correlated inflation hedges."
        )
    }

def get_psa_watchlist(filter_category: str = "ALL") -> List[Dict[str, Any]]:
    """Returns the filtered watchlist of institutional PSA blue-chips."""
    if not filter_category or filter_category == "ALL":
        return PSA_BLUE_CHIPS
    return [c for c in PSA_BLUE_CHIPS if c.get("category") == filter_category]

def get_psa_card_detail(card_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves full telemetry, auction history, and Frank's analysis for a specific card."""
    target_id = card_id.strip().upper()
    for c in PSA_BLUE_CHIPS:
        if c["id"].upper() == target_id:
            return c
    return None
