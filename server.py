"""
FitAccess Construction ERP
Main entry point for the Flask application.
"""

import os
from app.factory import create_app

app = create_app(os.environ.get('FLASK_ENV', 'development'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
