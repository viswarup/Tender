# Accuracy vs IOCL committee minutes (PQC1)

Scope: 5 bidders, 22 purchase orders evaluated by the committee.

## Headline

| Stage | Metric | Result |
|---|---|---|
| Discovery | Committee POs found by pipeline (recall) | **22/22 (100%)** |
| Extraction | PO date exact | **20/22 (91%)** |
| Extraction | PO value exact (±₹1) | **17/22 (77%)** |
| Extraction | Issuing organisation | **14/22 (64%)** |
| Judgement | Similar-item decision | **11/21 (52%)** |
| Judgement | Execution-proof decision | **14/20 (70%)** |
| Verdict | Bidder status matches committee | **3/5 (60%)** |
| Verdict | False accepts (unqualified bidder passed) | **2** |

Pipeline extracted 27 unique POs; 5 were not in the committee's evaluation (precision 22/27 (81%)).

## Verdicts

| Bidder | Committee | System | Match | System basis |
|---|---|---|---|---|
| ALANKAR DISTRIBUTORS | QUERIED | ACCEPTED | ❌ false accept | MSE Exemption (Udyam Registration) |
| ARMS PERIPHERALS | ACCEPTED | ACCEPTED | ✅ | Startup Exemption (DPIIT Recognition) |
| HEAMONS SYSTEMS | ACCEPTED | ACCEPTED | ✅ | Option C: 1 order(s) × ≥ ₹303,000 |
| LUXMI ENTERPRISES | ACCEPTED | ACCEPTED | ✅ | Option C: 1 order(s) × ≥ ₹303,000 |
| RAVECHI CARTRIDGE | REJECTED | ACCEPTED | ❌ false accept | MSE Exemption (Udyam Registration) |

## Field errors

| Bidder | PO | Field | Committee | System |
|---|---|---|---|---|
| ALANKAR DISTRIBUTORS | GEMC-511687706010310 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ALANKAR DISTRIBUTORS | GEMC-511687732777771 | organisation | | Unknown |
| ALANKAR DISTRIBUTORS | GEMC-511687732777771 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ALANKAR DISTRIBUTORS | GEMC-511687771151409 | value | ₹2,684,305 | ₹4,326,954 |
| ALANKAR DISTRIBUTORS | GEMC-511687771151409 | organisation | | Unknown |
| ALANKAR DISTRIBUTORS | GEMC-511687771151409 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ALANKAR DISTRIBUTORS | GEMC-511687731854057 | value | ₹1,979,332 | ₹2,211,032 |
| ALANKAR DISTRIBUTORS | GEMC-511687731854057 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ALANKAR DISTRIBUTORS | GEMC-511687778717530 | value | ₹483,320 | ₹1 |
| ALANKAR DISTRIBUTORS | GEMC-511687778717530 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ALANKAR DISTRIBUTORS | GEMC-511687732609862 | organisation | | N/A |
| ALANKAR DISTRIBUTORS | GEMC-511687732609862 | execution proof | NOT_SUBMITTED | SUBMITTED |
| ARMS PERIPHERALS | GEMC-511687712377171 | value | ₹3,633,750 | ₹0 |
| ARMS PERIPHERALS | GEMC-511687712377171 | date | | Unknown |
| ARMS PERIPHERALS | GEMC-511687712377171 | organisation | | Unknown |
| HEAMONS SYSTEMS | GEMC-511687756260137 | organisation | | Unknown |
| HEAMONS SYSTEMS | GEMC-511687778503573 | organisation | | Unknown |
| LUXMI ENTERPRISES | GEMC-511687754844117 | value | ₹918,575 | ₹0 |
| LUXMI ENTERPRISES | GEMC-511687754844117 | date | | Unknown |
| LUXMI ENTERPRISES | GEMC-511687757736947 | organisation | | Unknown |
| RAVECHI CARTRIDGE | GEMC-511687749581305 | organisation | | Unknown |
