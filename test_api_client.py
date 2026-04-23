"""
Client script to test both REST API and SOAP API endpoints
Demonstrates how to call the extraction APIs
"""

import requests
import json
from zeep import Client
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ExtractionAPIClient:
    """Client for communicating with extraction APIs"""
    
    def __init__(self, rest_url="http://localhost:8000", soap_url="http://localhost:8001/soap?wsdl"):
        """
        Initialize API client
        
        Parameters:
        - rest_url: URL of REST API (default: localhost:8000)
        - soap_url: URL of SOAP service WSDL (default: localhost:8001)
        """
        self.rest_url = rest_url
        self.soap_url = soap_url
        self.soap_client = None
        
    def call_rest_api(self, token_id, source_file_name, template_name):
        """
        Call extraction via REST API
        
        Parameters:
        - token_id: Unique identifier for the extraction job
        - source_file_name: Name of source file in SOURCE directory
        - template_name: Name of template file in TEMPLATE directory
        
        Returns:
        - JSON response with extraction results
        """
        try:
            logger.info(f"Calling REST API with token: {token_id}")
            
            url = f"{self.rest_url}/api/extract"
            
            payload = {
                "token_id": token_id,
                "source_file_name": source_file_name,
                "template_name": template_name
            }
            
            headers = {
                "Content-Type": "application/json"
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✓ REST API call successful - Status: {result.get('status')}")
                return result
            else:
                logger.error(f"✗ REST API error - Status Code: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return {
                    "status": "error",
                    "error_message": f"HTTP {response.status_code}: {response.text}",
                    "token_id": token_id
                }
        
        except Exception as e:
            logger.error(f"✗ REST API exception: {str(e)}")
            return {
                "status": "error",
                "error_message": str(e),
                "token_id": token_id
            }
    
    def call_soap_api(self, token_id, source_file_name, template_name):
        """
        Call extraction via SOAP API
        
        Parameters:
        - token_id: Unique identifier for the extraction job
        - source_file_name: Name of source file in SOURCE directory
        - template_name: Name of template file in TEMPLATE directory
        
        Returns:
        - SOAP response with extraction results
        """
        try:
            logger.info(f"Calling SOAP API with token: {token_id}")
            
            # Initialize SOAP client if not already done
            if self.soap_client is None:
                logger.info(f"Connecting to SOAP service at {self.soap_url}")
                self.soap_client = Client(wsdl=self.soap_url)
            
            # Prepare request object
            request_obj = {
                'token_id': token_id,
                'source_file_name': source_file_name,
                'template_name': template_name
            }
            
            # Call SOAP service
            response = self.soap_client.service.extract_data(request=request_obj)
            
            logger.info(f"✓ SOAP API call successful - Status: {response['status']}")
            
            return {
                "status": response.get('status'),
                "token_id": response.get('token_id'),
                "result_file_name": response.get('result_file_name'),
                "message": response.get('message'),
                "result_summary": response.get('result_summary'),
                "error_message": response.get('error_message')
            }
        
        except Exception as e:
            logger.error(f"✗ SOAP API exception: {str(e)}")
            return {
                "status": "error",
                "error_message": str(e),
                "token_id": token_id
            }
    
    def check_rest_health(self):
        """Check if REST API is healthy"""
        try:
            response = requests.get(f"{self.rest_url}/api/health", timeout=5)
            if response.status_code == 200:
                logger.info("✓ REST API is healthy")
                return True
            else:
                logger.warning(f"✗ REST API health check failed: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"✗ REST API unreachable: {str(e)}")
            return False
    
    def get_rest_info(self):
        """Get REST API information"""
        try:
            response = requests.get(f"{self.rest_url}/api/info", timeout=5)
            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}


def demo_rest_api():
    """Demo function for REST API"""
    logger.info("\n" + "="*60)
    logger.info("REST API DEMO")
    logger.info("="*60)
    
    client = ExtractionAPIClient()
    
    # Check health
    if not client.check_rest_health():
        logger.warning("REST API is not running. Start it with: python -m uvicorn api_server:app --host 0.0.0.0 --port 8000")
        return
    
    # Get API info
    logger.info("\nFetching API info...")
    info = client.get_rest_info()
    logger.info(f"API Info: {json.dumps(info, indent=2)}")
    
    # Call extraction API with sample data
    logger.info("\nCalling extraction endpoint...")
    result = client.call_rest_api(
        token_id="rest_demo_001",
        source_file_name="EMB_improvedexample_2025.xlsx",  # Update with actual file
        template_name="TEMPEX.json"  # Update with actual template
    )
    
    logger.info(f"\nExtraction Result:")
    logger.info(json.dumps(result, indent=2)[:500] + "...")  # Print first 500 chars


def demo_soap_api():
    """Demo function for SOAP API"""
    logger.info("\n" + "="*60)
    logger.info("SOAP API DEMO")
    logger.info("="*60)
    
    client = ExtractionAPIClient()
    
    logger.info("\nCalling SOAP extraction endpoint...")
    logger.info("Note: Ensure SOAP server is running separately")
    logger.info("Start SOAP server with: python -m spyne.server.null api_server.soap_application")
    
    try:
        result = client.call_soap_api(
            token_id="soap_demo_001",
            source_file_name="EMB_improvedexample_2025.xlsx",  # Update with actual file
            template_name="TEMPEX.json"  # Update with actual template
        )
        
        logger.info(f"\nSOAP Extraction Result:")
        logger.info(json.dumps(result, indent=2))
    
    except Exception as e:
        logger.error(f"SOAP API error: {str(e)}")


def demo_both_apis():
    """Demo function comparing both APIs"""
    logger.info("\n" + "="*60)
    logger.info("COMPARING REST vs SOAP APIS")
    logger.info("="*60)
    
    client = ExtractionAPIClient()
    
    # Test data
    token_id = "comparison_001"
    source_file = "EMB_improvedexample_2025.xlsx"  # Update with actual file
    template_file = "TEMPEX.json"  # Update with actual template
    
    logger.info(f"\nTest Parameters:")
    logger.info(f"  Token ID: {token_id}")
    logger.info(f"  Source File: {source_file}")
    logger.info(f"  Template: {template_file}")
    
    # REST API call
    logger.info("\n--- REST API Call ---")
    rest_result = client.call_rest_api(token_id, source_file, template_file)
    logger.info(f"Status: {rest_result.get('status')}")
    logger.info(f"Message: {rest_result.get('message')}")
    
    # SOAP API call
    logger.info("\n--- SOAP API Call ---")
    soap_result = client.call_soap_api(token_id, source_file, template_file)
    logger.info(f"Status: {soap_result.get('status')}")
    logger.info(f"Message: {soap_result.get('message')}")


if __name__ == "__main__":
    # Uncomment the demo you want to run
    
    print("\nAvailable demos:")
    print("1. demo_rest_api() - Test REST API only")
    print("2. demo_soap_api() - Test SOAP API only")
    print("3. demo_both_apis() - Compare both APIs")
    print("\nFor REST API, run in separate terminal:")
    print("  python -m uvicorn api_server:app --host 0.0.0.0 --port 8000")
    print("\nFor SOAP API (optional), run in separate terminal:")
    print("  python -m spyne.server.null api_server.soap_application")
    print("\n" + "="*60)
    
    # Run demo
    demo_rest_api()
    # demo_soap_api()
    # demo_both_apis()
