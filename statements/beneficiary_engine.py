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
    PATTERNS = {
        # ── IMPS slash-delimited: IMPS/UTR/NAME/ACCOUNT/...
        # e.g. IMPS/609544947606/NEW SADHI MOBILE AND ELECTRONICS/7874223322/...
        # Captures field 3 (the human name) — skips UTR (all digits) and junk like 'Unregistered'
        'imps_slash': r'IMPS/\d+/(?!Unregistered)([A-Za-z][A-Za-z\s&\.\-]+?)/',

        # ── BillDesk payments: Bill Payment/BillDesk/IFSC.../ref.../MOB/null
        # e.g. Bill Payment/BillDesk/ICIC00000NATSI/6096 19239590/MOB/null
        # Counterparty = IFSC bank code (ICIC00000NATSI → ICICI Bank)
        'billdesk': r'Bill\s+Payment/BillDesk/([A-Z]{4}\d*[A-Z0-9]+?)/',

        # ── HIGHLY STRUCTURED MULTI-FIELD PATTERNS (PDFs / Advanced statements) ──
        # e.g. IMPS:331306183624:059621010000023:DUNESTUDYABROAD
        # e.g. RTGS/UBINR22025032101058301/COHERENT RMC PRIV
        # e.g. CLG/573818/ANURADHA CHAVAN/THE MUNCIPAL CO-OP.BANK
        # e.g. IB/RTGS/SRCBH23339232404/NIRMAL MA/Transfer
        'structured_colon_slash': r'(?:RTGS|NEFT|CLG|IB/RTGS|IB/NEFT)(?:[:/][A-Z0-9]+)+[:/]([A-Za-z][A-Za-z\s\.&\-]+?)(?:[:/]|$)',

        # ── STANDARD SPACE/HYPHEN FORMATS (RPTs / Basic statements) ──────────────
        # e.g. NEFT 000520293131 DAMODAR LAXMINARAYAN HEGDE.
        # e.g. RTGSALI ASGAR IQBALHDFCR520190
        'neft_rtgs': r'(?:NEFT|RTGS)[-\s]*(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:\s+A/C|\s+LTD|[-\d/]|$)',

        # By Inst (e.g. By Inst.6/HDFCBANK/)
        'inst': r'By\s*Inst\.?\s*[\d]+/?([A-Za-z][A-Za-z\s\.&\-]+?)(?:/|$)',

        # IMPS standard (colon or space-separated, non-slash format)
        'imps': r'IMPS[-\s]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:[-\d/]|$)',

        # UPI
        'upi': r'UPI[-\s/]+(?:[A-Z0-9]*\d[A-Z0-9]*[-\s/]+)?(?:[A-Za-z0-9@\.\-_]+[-\s/]+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:[-\d/]|$)',

        # BY [BENEFICIARY NAME] or BY TRF [BENEFICIARY NAME]
        'by_credit': r'^BY\s+(?:TRF\s+)?([A-Za-z][A-Za-z\s\.&\-]+?)(?:$|/|[-\d])',

        # IB/NEFT fallback
        'ib_neft': r'(?:IB/NEFT|IB|NEFT)/[A-Z0-9]+/([A-Za-z][A-Za-z\s\.&\-]+?)(?:/|$)',

        # Generic company names
        'company_name': r'([A-Za-z\s]+(?:PVT|LTD|PRIVATE|LIMITED|CORP|CORPORATION)(?:\s+LTD)?)',

        # Dash or special character prefixed names (e.g. --MAHARASHTRA STATE ELECTR)
        'dash_prefix': r'^[-:\s]+([A-Za-z][A-Za-z\s\.&\-]+?)(?:$)',

        # Full names (catch-all for narrations that are purely a name)
        'full_name': r'^([A-Za-z][A-Za-z\s\.&\-]{2,})$',

        # Cheque
        'cheque': r'CHQ\s*(?:NO\s*)?[\d\-]+\s+([A-Za-z][A-Za-z\s\.&\-]+?)(?:$|/)',

        # Account number
        'account_number': r'(\d{7,18})',
    }
    
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

    # Common banking terms to exclude
    EXCLUSION_KEYWORDS = [
        'TRANSFER', 'PAYMENT', 'DEBIT', 'CREDIT', 'BALANCE', 'CHARGES',
        'INTEREST', 'CLEARING', 'MISC', 'OTHERS', 'UNKNOWN', 'CASH'
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
    
    def __init__(self, statement, transaction_threshold=100000, confidence_threshold='HIGH'):
        self.statement = statement
        self.transaction_threshold = Decimal(str(transaction_threshold))
        self.confidence_threshold = confidence_threshold
        self.ollama_url = 'http://localhost:11434/api/generate'
        self.results = []
        self.unresolved = []
    
    # ===== LAYER 1: RULE-BASED EXTRACTION =====

    def layer1_extract(self, transaction):
        """Try to extract beneficiary using pattern matching."""
        narration = (transaction.narration_raw or '').strip()
        
        if not narration or len(narration) < 2:
            return None
        
        # Try each pattern in order
        for pattern_name, pattern in self.PATTERNS.items():
            try:
                match = re.search(pattern, narration, re.IGNORECASE)
                if match:
                    extracted = match.group(1).strip()
                    
                    # Filter out generic terms
                    if self._is_valid_beneficiary_name(extracted):
                        beneficiary_type = self._classify_beneficiary_type(extracted)
                        confidence = self._calculate_layer1_confidence(pattern_name, narration)
                        
                        return {
                            'beneficiary_name': self._normalize_name(extracted),
                            'beneficiary_type': beneficiary_type,
                            'confidence': confidence,
                            'layer': 'LAYER_1',
                            'extraction_basis': f'Pattern: {pattern_name}',
                            'pattern_used': pattern_name,
                        }
            except Exception as e:
                continue
        
        return None
        
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
        
        # Reject pure bank/generic terms
        if name.upper() in self.EXCLUSION_KEYWORDS:
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
        """Normalize beneficiary name (uppercase, strip extra spaces)."""
        return ' '.join(name.upper().split())
    
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