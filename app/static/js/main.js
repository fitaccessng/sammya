/* Main JavaScript for FitAccess ERP */

// Global utility functions

/**
 * Format currency for display
 */
function formatCurrency(value) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD'
    }).format(value);
}

/**
 * Format date for display
 */
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

/**
 * Show notification toast
 */
function showNotification(message, type = 'info') {
    const typeClass = {
        'success': 'bg-green-100 border-green-400 text-green-800',
        'error': 'bg-red-100 border-red-400 text-red-800',
        'warning': 'bg-amber-100 border-amber-400 text-amber-800',
        'info': 'bg-blue-100 border-blue-400 text-blue-800'
    }[type] || 'bg-slate-100 border-slate-400 text-slate-800';
    
    const toast = document.createElement('div');
    toast.className = `fixed top-4 right-4 z-50 ${typeClass} px-4 py-3 rounded border shadow-lg`;
    toast.innerHTML = `
        ${message}
        <button onclick="this.parentElement.remove()" class="float-right font-bold ml-4">&times;</button>
    `;
    document.body.appendChild(toast);
    
    setTimeout(() => toast.remove(), 5000);
}

/**
 * Fetch with error handling
 */
async function fetchJSON(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Request failed');
        }
        
        return await response.json();
    } catch (error) {
        showNotification(error.message, 'error');
        throw error;
    }
}

/**
 * Load partial HTML from server
 */
async function loadPartial(url, targetSelector) {
    try {
        const response = await fetch(url);
        if (!response.ok) throw new Error('Failed to load partial');
        
        const html = await response.text();
        const target = document.querySelector(targetSelector);
        if (target) {
            target.innerHTML = html;
        }
    } catch (error) {
        console.error('Error loading partial:', error);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Add any global event listeners here
    console.log('FitAccess ERP initialized');
});
