"""
Beneficiary Identification Engine
==================================
Three-layer hybrid identification:
  Layer 1: Rule-based extraction from structured narrations
  Layer 2: Local LLM (Ollama) for complex cases
  Layer 3: Analyst review queue
"""

import re
import json
import requests
from decimal import Decimal
from datetime import datetime
from difflib import SequenceMatcher
from decimal import Decimal

class BeneficiaryEngine:
    """Identifies beneficiaries from transaction narrations."""
    
    # Layer 1: Patterns for structured narrations
    # PATTERNS = {
    #     # NEFT/RTGS: "NEFT-Full Name Here" or "RTGSNIRMAL LIFESTYLE LTD"
    #     'neft_rtgs': r'(?:NEFT|RTGS)[-\s]+([A-Za-z\s]+?)(?:\s+A/C|\s+LTD|$|/)',
        
    #     # IMPS: "IMPS-Name Here"
    #     'imps': r'IMPS[-\s]+([A-Za-z\s]+?)(?:$|/)',
        
    #     # UPI: "UPI-Name Here"
    #     'upi': r'UPI[-\s]+([A-Za-z\s]+?)(?:$|/)',
        
    #     # Cheque: "CHQ NO 12345 Full Name Here"
    #     'cheque': r'CHQ\s*(?:NO\s*)?[\d\-]+\s+([A-Za-z\s]+?)(?:$|/)',
        
    #     # BY: "BY Full Name Here"
    #     'by_credit': r'BY\s+([A-Za-z\s]+?)(?:$|/)',
        
    #     # Account number
    #     'account_number': r'(\d{7,18})',
    # --- PATTERNS COMMENTED OUT FOR UNIVERSAL EXTRACTOR ---
    # PATTERNS = {
    #     'imps_slash': r'IMPS/\d+/(?!Unregistered)([A-Za-z][A-Za-z\s&\.\-]+?)/',
    #     'billdesk': r'Bill\s+Payment/BillDesk/([A-Z]{4}\d*[A-Z0-9]+?)/',
    #     'structured_colon_slash': r'(?:RTGS|NEFT|CLG|IB/RTGS|IB/NEFT)(?:[:/][A-Z0-9]+)+[:/]([A-Za-z][A-Za-z\s\.&\-]+?)(?:[:/]|$)',
    #     'neft_rtgs': r'(?:NEFT|RTGS)[-\s]*(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:\s+A/C|\s+LTD|[-\d/]|$)',
    #     'inst': r'By\s*Inst\.?\s*[\d]+/?([A-Za-z][A-Za-z\s\.&\-]+?)(?:/|$)',
    #     'imps': r'IMPS[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:[-\d/]|$)',
    #     'upi': r'UPI[-\s/]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s/]+)?(?:[A-Za-z0-9@\.\-_]+[-\s/]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:[-\d/]|$)',
    #     'by_credit': r'^BY\s+(?:TRF\s+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:$|/|[-\d])',
    #     'ib_neft': r'(?:IB/NEFT|IB|NEFT)/[A-Z0-9]+/([A-Za-z][A-Za-z\s\.&\-]+?)(?:/|$)',
    #     'company_name': r'([A-Za-z\s]+(?:PVT|LTD|PRIVATE|LIMITED|CORP|CORPORATION)(?:\s+LTD)?)',
    #     'dash_prefix': r'^[-:\s]+([A-Za-z][A-Za-z\s\.&\-]+?)(?:$)',
    #     'full_name': r'^([A-Za-z][A-Za-z\s\.&\-]{2,})$',
    #     'cheque': r'CHQ\s*(?:NO\s*)?[\d\-]+\s+([A-Za-z][A-Za-z\s\.&\-]+?)(?:$|/)',
    #     'account_number': r'(\d{7,18})',
    # }
    
    # --- PREVIOUS CODE (Kept for safety) ---
    # PATTERNS = {
    #     # NEFT/RTGS with full name (handles optional alphanumeric UTR before or after the name, and allows missing spaces)
    #     'neft_rtgs': r'(?:NEFT|RTGS)[-\s]*(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z\s]+?)(?:\s+A/C|\s+LTD|[-\d/]|$)',
    #     'inst': r'By\s*Inst\.?\s*[\d]+/?([A-Za-z\s]+?)(?:/|$)',
    #     'imps': r'IMPS[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z\s]+?)(?:[-\d/]|$)',
    #     'upi': r'UPI[-\s/]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s/]+)?(?:[A-Za-z0-9@\.\-_]+[-\s/]+)?([A-Za-z\s]+?)(?:[-\d/]|$)',
    #     'by_credit': r'^BY[-\s]+([A-Za-z\s]+?)(?:$|/|[-\d])',
    #     'company_name': r'([A-Za-z\s]+(?:PVT|LTD|PRIVATE|LIMITED|CORP|CORPORATION)(?:\s+LTD)?)',
    #     'ib_neft': r'(?:IB/NEFT|IB|NEFT)/[A-Z0-9]+/([A-Za-z\s]+?)(?:/|$)',
    #     'full_name': r'^([A-Za-z\s\.]{3,})$',
    #     'cheque': r'CHQ\s*(?:NO\s*)?[\d\-]+\s+([A-Za-z\s]+?)(?:$|/)',
    #     'account_number': r'(\d{7,18})',
    # }
    
    # --- PREVIOUS CODE (Kept for safety) ---
    # PATTERNS = {
    #     # NEFT/RTGS with full name (handles optional alphanumeric UTR before or after the name)
    #     'neft_rtgs': r'(?:NEFT|RTGS)[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z\s]+?)(?:\s+A/C|\s+LTD|[-\d/]|$)',
    #     
    #     # IMPS
    #     'imps': r'IMPS[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z\s]+?)(?:[-\d/]|$)',
    #     
    #     # UPI
    #     'upi': r'UPI[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z\s]+?)(?:[-\d/]|$)',
    #     
    #     # Generic company names
    #     'company_name': r'([A-Za-z\s]+(?:PVT|LTD|PRIVATE|LIMITED|CORP|CORPORATION)(?:\s+LTD)?)',
    #     
    #     # IB/NEFT pattern (e.g. IB/NEFT/SRCB024111362874/NIRMAL MA/Transfer)
    #     'ib_neft': r'(?:IB/NEFT|IB|NEFT)/[A-Z0-9]+/([A-Za-z\s]+?)(?:/|$)',
    #     
    #     # Full names (catch all for narrations that are just names)
    #     'full_name': r'^([A-Za-z\s\.]{3,})$',
    #     
    #     # Cheque
    #     'cheque': r'CHQ\s*(?:NO\s*)?[\d\-]+\s+([A-Za-z\s]+?)(?:$|/)',
    #     
    #     # Account number
    #     'account_number': r'(\d{7,18})',
    # }

    # Universal Exclusion Keywords for Subtraction Logic
    EXCLUSION_KEYWORDS = [
        r'\bIMPS\b', r'\bNEFT\b', r'\bRTGS\b', r'\bUPI\b', r'\bTRANSFER\b', r'\bPAYMENT\b', 
        r'\bDEBIT\b', r'\bCREDIT\b', r'\bBALANCE\b', r'\bCHARGES\b', r'\bINTEREST\b', 
        r'\bCLEARING\b', r'\bMISC\b', r'\bOTHERS\b', r'\bUNKNOWN\b', r'\bCASH\b', 
        r'\bUNREGISTERED\b', r'\bOUT\b', r'\bIN\b', r'\bEMI\b', r'\bPENDING\b', 
        r'\bNULL\b', r'\bMOB\b', r'\bTRF\b', r'\bBY\b', r'\bBILL\b', r'\bTRANSACTION\b'
    ]
    
    BENEFICIARY_TYPES = {
        'COMPANY': ['LTD', 'PVT', 'INDUSTRIES', 'MANUFACTURING', 'CORP', 'CORPORATION'],
        'BANK': ['BANK', 'HDFC', 'ICICI', 'AXIS', 'SBI', 'YESBANK', 'KOTAK'],
        'GOVERNMENT': ['GOVERNMENT', 'DEPT', 'MINISTRY', 'MUNICIPAL', 'CORPORATION'],
        'INDIVIDUAL': []  # Default if no other match
    }
    
    CONFIDENCE_THRESHOLDS = {
        'HIGH': 0.85,
        'MEDIUM': 0.60,
        'LOW': 0.0
    }
    
    BANK_CODE_MAP = {
        'HDFC': 'HDFC BANK',
        'KOTA': 'KOTAK MAHINDRA BANK',
        'ICIC': 'ICICI BANK',
        'AUBA': 'AU SMALL FINANCE BANK',
        'RBLB': 'RBL BANK',
        'SBIC': 'STATE BANK OF INDIA',
        'AXIS': 'AXIS BANK',
        'IBKL': 'IDBI BANK',
        'BARB': 'BANK OF BARODA',
        'PUNB': 'PUNJAB NATIONAL BANK',
        'CNRB': 'CANARA BANK',
        'UTIB': 'AXIS BANK',
        'YESB': 'YES BANK',
        'ANDB': 'ANDHRA BANK',
        'CORP': 'CORPORATION BANK',
        'IDIB': 'INDIAN BANK',
        'IOBA': 'INDIAN OVERSEAS BANK',
        'ORBC': 'ORIENTAL BANK OF COMMERCE',
        'UBIN': 'UNION BANK OF INDIA',
        'VIJB': 'VIJAYA BANK',
        'SDRB': 'SIDBI',
        'SYNB': 'SYNDICATE BANK',
        'ALLA': 'ALLAHABAD BANK',
        'FDRL': 'FEDERAL BANK',
        'HSBC': 'HSBC BANK',
        'SCBL': 'STANDARD CHARTERED BANK',
        'KKBK': 'KOTAK MAHINDRA BANK',
        'INDB': 'INDUSIND BANK',
        'MAHB': 'BANK OF MAHARASHTRA',
        'UCOB': 'UCO BANK',
        'CBIN': 'CENTRAL BANK OF INDIA',
        'BOTM': 'MUFG BANK',
        'CITI': 'CITIBANK',
        'DBSS': 'DBS BANK',
        'ESAF': 'ESAF SMALL FINANCE BANK',
        'JSBP': 'JANALAKSHMI COOPERATIVE BANK',
        'KARB': 'KARNATAKA BANK',
        'KVBL': 'KARUR VYSYA BANK',
        'LAVB': 'LAXMI VILAS BANK',
        'NBLD': 'NOIDA COMMERCIAL COOPERATIVE BANK',
        'PMEC': 'PRIME COOPERATIVE BANK',
        'SIBL': 'SOUTH INDIAN BANK',
        'TJSB': 'TJSB SAHAKARI BANK',
        'TMBL': 'TAMILNAD MERCANTILE BANK',
        'VARB': 'VARACHHA COOPERATIVE BANK',
        'VYSA': 'ING VYSYA BANK',
    }
    
    def __init__(self, statement, transaction_threshold=100000, confidence_threshold='HIGH'):
        self.statement = statement
        self.transaction_threshold = Decimal(str(transaction_threshold))
        self.confidence_threshold = confidence_threshold
        self.ollama_url = 'http://localhost:11434/api/generate'
        self.results = []
        self.unresolved = []
    
    # ===== LAYER 1: RULE-BASED EXTRACTION =====

    def layer1_extract(self, transaction):
        """Universal Extraction: Splitting, Noise Subtraction, and Scoring."""
        narration = (transaction.narration_raw or '').strip()
        
        if not narration or len(narration) < 2:
            return None
            
        ex_re = re.compile('|'.join(self.EXCLUSION_KEYWORDS), re.IGNORECASE)
        # Regex to remove reference numbers (5+ digits), IFSC codes, date strings
        ref_re = re.compile(r'\b\d{5,}\b|\b[A-Z]{4}0[A-Z0-9]{6}\b|\b\d{2}-\d{2}-\d{4}\b', re.IGNORECASE)

        # Split by strong delimiters (/ or : or hyphen-with-spaces or multiple spaces)
        segments = re.split(r'/|:| - | -|  +', narration)
        if len(segments) == 1:
            segments = [narration]
            
        candidates = []
        for seg in segments:
            # Subtraction phase
            seg_clean = ex_re.sub(' ', seg)
            seg_clean = ref_re.sub(' ', seg_clean)
            seg_clean = re.sub(r'[\-\.]+', ' ', seg_clean)

            # Remove ANY numeric token — plain, comma-grouped, or decimal
            # e.g. "10,10,000", "1234", "12.50" — none of these are ever
            # part of a real beneficiary name.
            seg_clean = re.sub(r'\b[\d,\.]*\d[\d,\.]*\b', ' ', seg_clean)

            # Strip mixed alphanumeric tokens (e.g. "SRCB024111362874", "SAA34")
            # These are reference/account codes, not name fragments.
            seg_clean = re.sub(r'\b[A-Z]{1,6}\d+[A-Z0-9]*\b', ' ', seg_clean)

            seg_clean = re.sub(r'\s+', ' ', seg_clean).strip()

            # Early validation: only keep segments that look like real names.
            # This prevents junk tokens from outscoring valid names in the
            # scoring phase (e.g. "SRCB024111362874" is 16 chars and would
            # beat "NIRMAL MA" on pure length if not filtered here).
            if len(seg_clean) > 3 and not seg_clean.isdigit() and self._is_valid_beneficiary_name(seg_clean):
                candidates.append(seg_clean)

        if not candidates:
            return None

        # Scoring phase
        def score_candidate(c):
            s = 0
            c_up = c.upper()
            if 'BANK' in c_up: s += 5
            if any(k in c_up for k in ['PVT', 'LTD', 'INDUSTRIES']): s += 5
            # Strong bonus if purely alphabetic + multi-word (real person / company name)
            if re.fullmatch(r'[A-Za-z\s]+', c):
                s += 4
                word_count = len(c.split())
                if word_count >= 2:
                    s += 3  # multi-word names are much more likely to be real
            # Tie breaker: length
            s += len(c) * 1.0
            return s

        best_candidate = max(candidates, key=score_candidate)
        
        # If the best candidate is still garbage, fallback
        if not self._is_valid_beneficiary_name(best_candidate):
            return None
            
        beneficiary_type = self._classify_beneficiary_type(best_candidate)
        
        return {
            'beneficiary_name': self._normalize_name(best_candidate),
            'beneficiary_type': beneficiary_type,
            'confidence': 0.90, # Universal rule confidence
            'layer': 'LAYER_1',
            'extraction_basis': 'Universal Tokenization & Subtraction',
            'pattern_used': 'universal_extractor',
        }
        
    # def layer1_extract(self, transaction):
    #     """Try to extract beneficiary using pattern matching."""
    #     narration = (transaction.narration_raw or '').strip()
        
    #     if not narration or len(narration) < 5:
    #         return None
        
    #     # Try each pattern
    #     for pattern_name, pattern in self.PATTERNS.items():
    #         try:
    #             match = re.search(pattern, narration, re.IGNORECASE)
    #             if match:
    #                 extracted = match.group(1).strip()
                    
    #                 # Validate extracted text
    #                 if self._is_valid_beneficiary_name(extracted):
    #                     beneficiary_type = self._classify_beneficiary_type(extracted)
    #                     confidence = self._calculate_layer1_confidence(pattern_name, narration)
                        
    #                     return {
    #                         'beneficiary_name': self._normalize_name(extracted),
    #                         'beneficiary_type': beneficiary_type,
    #                         'confidence': confidence,
    #                         'layer': 'LAYER_1',
    #                         'extraction_basis': f'Extracted from {pattern_name} pattern',
    #                         'pattern_used': pattern_name,
    #                     }
    #         except Exception as e:
    #             continue
        
    #     return None
    
    def _is_valid_beneficiary_name(self, name):
        """Check if extracted name is likely a valid beneficiary."""
        if not name or len(name) < 2:
            return False
        
        # Reject if too long (likely garbage)
        if len(name) > 100:
            return False
        
        # Reject if entirely numeric
        if name.isdigit():
            return False
        
        # --- NEW: Reject UTR codes / account numbers that look like 'ICICR5202503210031769'
        # These are long alphanumeric strings with NO spaces and contain many digits.
        # A real person/company name always has spaces (or is a short abbreviation).
        digits_in_name = sum(c.isdigit() for c in name)
        
        # Reject if digits make up more than 40% of a name longer than 8 chars
        if len(name) > 8 and digits_in_name / len(name) > 0.40:
            return False
        
        # Reject if the whole name is a single token (no spaces) longer than 20 chars
        # Real names have spaces; UTR codes / account numbers don't
        if ' ' not in name.strip() and len(name) > 20:
            return False
        
        # Reject if name ends with a standalone number (e.g. "NLL KALYAN 243320")
        if re.search(r'\s+\d+$', name):
            return False
        
        # Reject if name is only 1 word and that word contains digits (e.g. "SAA34")
        words = name.strip().split()
        if len(words) == 1 and any(c.isdigit() for c in words[0]):
            return False
        
        # Reject pure bank/generic terms
        word_set = set(w.upper() for w in words if w)
        clean_exclusions = set(k.replace(r'\b', '') for k in self.EXCLUSION_KEYWORDS)
        if word_set and all(w in clean_exclusions for w in word_set):
            return False
        
        # Must have at least one letter
        if not any(c.isalpha() for c in name):
            return False
        
        return True
    
    def _classify_beneficiary_type(self, name):
        """Classify beneficiary as COMPANY, INDIVIDUAL, BANK, or GOVERNMENT."""
        name_upper = name.upper()
        
        for btype, keywords in self.BENEFICIARY_TYPES.items():
            if any(kw in name_upper for kw in keywords):
                return btype
        
        return 'INDIVIDUAL'
    
    def _normalize_name(self, name):
        """Normalize beneficiary name (uppercase, strip extra spaces, resolve bank codes)."""
        name_clean = ' '.join(name.upper().split())
        
        # Resolve IFSC/routing codes (e.g. HDFCOOOOOONATW1, ICICOOOOOONATSI) to clean Bank Names
        if re.match(r'^[A-Z]{4}[A-Z0-9]{7,11}$', name_clean):
            prefix = name_clean[:4]
            if prefix in self.BANK_CODE_MAP:
                return self.BANK_CODE_MAP[prefix]
                
        return name_clean
    
    def _calculate_layer1_confidence(self, pattern_name, narration):
        """Assign confidence based on pattern and narration quality."""
        confidence_map = {
            # Highly structured / fixed position formats — highest confidence
            'imps_slash': 0.95,          # HIGH — direct name field from IMPS slash format
            'billdesk': 0.90,            # HIGH — IFSC code uniquely identifies bank/recipient
            'structured_colon_slash': 0.95, # HIGH
            'account_number': 0.95,      # HIGH
            'neft_rtgs': 0.90,           # HIGH
            'company_name': 0.90,        # HIGH
            'ib_neft': 0.90,             # HIGH

            # Formats bumped to >= 0.85 so they automatically bypass the review queue
            'inst': 0.85,                # HIGH
            'full_name': 0.85,           # HIGH
            'upi': 0.85,                 # HIGH
            'cheque': 0.85,              # HIGH
            'dash_prefix': 0.85,         # HIGH
            'by_credit': 0.85,           # HIGH
            'imps': 0.85,                # HIGH
        }
        return confidence_map.get(pattern_name, 0.60)
    
    # ===== LAYER 2: OLLAMA LLM =====
    
    def layer2_ollama_extract(self, transaction):
        """Use local Ollama LLM for complex narrations."""
        try:
            narration = (transaction.narration_raw or '').strip()
            amount = transaction.debit or transaction.credit or 0
            txn_type = 'DEBIT' if transaction.debit else 'CREDIT'
            
            # Construct prompt
            prompt = f"""You are a financial data extraction assistant.
Extract the beneficiary from the following Indian bank statement narration.

Narration: {narration}
Transaction type: {txn_type}
Amount: INR {amount}

Return ONLY a JSON object with these fields:
{{ "beneficiary_name": "extracted name or null",
"beneficiary_type": "COMPANY / INDIVIDUAL / BANK / GOVERNMENT / UNKNOWN",
"confidence": "HIGH / MEDIUM / LOW",
"extraction_basis": "one sentence explaining what you identified and why"}}

If you cannot identify a beneficiary with reasonable confidence, set beneficiary_name to null and confidence to LOW.
Do not guess. Do not fabricate."""
            
            # Call Ollama
            response = requests.post(
                self.ollama_url,
                json={
                    'model': 'mistral',  # or 'llama2' - configurable
                    'prompt': prompt,
                    'stream': False,
                    'temperature': 0.3,  # Low temperature for consistency
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                response_text = result.get('response', '').strip()
                
                # Extract JSON from response
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group())
                        
                        if data.get('beneficiary_name'):
                            return {
                                'beneficiary_name': self._normalize_name(data['beneficiary_name']),
                                'beneficiary_type': data.get('beneficiary_type', 'UNKNOWN'),
                                'confidence': self._map_confidence_string(data.get('confidence', 'LOW')),
                                'layer': 'LAYER_2_OLLAMA',
                                'extraction_basis': data.get('extraction_basis', 'LLM extraction'),
                                'raw_response': response_text,
                            }
                    except json.JSONDecodeError:
                        pass
        
        except requests.exceptions.ConnectionError:
            # Ollama not running
            return None
        except Exception as e:
            print(f"Ollama extraction error: {e}")
            return None
        
        return None
    
    def _map_confidence_string(self, conf_str):
        """Map confidence string to numeric value."""
        mapping = {
            'HIGH': 0.90,
            'MEDIUM': 0.70,
            'LOW': 0.40,
        }
        return mapping.get(conf_str.upper(), 0.40)
    
    # ===== LAYER 3: ANALYST REVIEW =====
    
    def create_review_queue_item(self, transaction, layer1_result=None, layer2_result=None):
        """Create an item for analyst review."""
        return {
            'transaction_id': transaction.id,
            'txn_date': transaction.txn_date.isoformat() if transaction.txn_date else None,
            'amount': str(transaction.debit or transaction.credit),
            'txn_type': 'DEBIT' if transaction.debit else 'CREDIT',
            'narration_raw': transaction.narration_raw,
            'layer1_result': layer1_result,
            'layer2_result': layer2_result,
            'status': 'PENDING_REVIEW',
            'analyst_assignment': None,
            'created_at': datetime.now().isoformat(),
        }
    
    # ===== MAIN ORCHESTRATION =====
    
    def run_identification(self, transactions=None):
        """Run full three-layer identification pipeline."""
        if transactions is None:
            # Get all transactions above threshold
            transactions = self.statement.transactions.filter(
                debit__gte=self.transaction_threshold
            ) | self.statement.transactions.filter(
                credit__gte=self.transaction_threshold
            )
        
        for txn in transactions:
            # Try Layer 1
            layer1_result = self.layer1_extract(txn)
            
            if layer1_result and layer1_result['confidence'] >= self.CONFIDENCE_THRESHOLDS[self.confidence_threshold]:
                self.results.append({
                    'transaction_id': txn.id,
                    'result': layer1_result,
                    'status': 'IDENTIFIED_LAYER1'
                })
                continue
            
            # Try Layer 2 (Ollama)
            layer2_result = self.layer2_ollama_extract(txn)
            
            if layer2_result and layer2_result['confidence'] >= self.CONFIDENCE_THRESHOLDS[self.confidence_threshold]:
                self.results.append({
                    'transaction_id': txn.id,
                    'result': layer2_result,
                    'status': 'IDENTIFIED_LAYER2'
                })
                continue
            
            # Layer 3: Queue for analyst
            queue_item = self.create_review_queue_item(txn, layer1_result, layer2_result)
            self.unresolved.append(queue_item)
        
        return {
            'identified_count': len(self.results),
            'unresolved_count': len(self.unresolved),
            'results': self.results,
            'review_queue': self.unresolved,
        }
    
    def get_statistics(self):
        """Return identification statistics."""
        return {
            'total_eligible': len(self.results) + len(self.unresolved),
            'identified_layer1': sum(1 for r in self.results if r['status'] == 'IDENTIFIED_LAYER1'),
            'identified_layer2': sum(1 for r in self.results if r['status'] == 'IDENTIFIED_LAYER2'),
            'pending_analyst': len(self.unresolved),
            'identification_rate': f"{(len(self.results) / (len(self.results) + len(self.unresolved)) * 100) if (len(self.results) + len(self.unresolved)) > 0 else 0:.1f}%",
        }