// Utility to get CSS variable value
function getVar(v) {
  return getComputedStyle(document.documentElement).getPropertyValue(v).trim();
}

// Chart.js theme colors
const chartColors = {
  primary:  getVar('--color-primary') || '#3498db',
  success:  getVar('--color-success') || '#27ae60',
  warning:  getVar('--color-warning') || '#f39c12',
  danger:   getVar('--color-danger')  || '#e74c3c',
  info:     getVar('--color-info')    || '#17a2b8'
};

function hideSpinner(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = 'none';
}
// --- Chart rendering (AJAX) ---
document.addEventListener('DOMContentLoaded', () => {
  // For this demo, use window.globals injected by template (SSR fallback); AJAX for progressive enhancement
  // Example: fetch('/admin/analytics/data?...') and re-render charts

  // Exam Chart
  if (window.examLabels) {
    hideSpinner('examChartSpinner');
    new Chart(document.getElementById('examChart'), {
      type: 'bar',
      data: {
        labels: window.examLabels,
        datasets: [{
          label: 'Avg Score (%)',
          data: window.examScores,
          backgroundColor: chartColors.primary
        }]
      },
      options: {
        responsive: true,
        indexAxis: 'y',
        scales: { x: { beginAtZero: true, max: 100 } },
        plugins: { legend: { display: false } },
        onClick: (e, elements) => {
          if(elements.length) {
            const idx = elements[0].index;
            const examTitle = window.examLabels[idx];
            window.location.href = `/admin/analytics/exam/${encodeURIComponent(examTitle)}`;
          }
        }
      }
    });
  }

  // Pass/Fail Chart
  if (window.passCount !== undefined) {
    hideSpinner('passFailChartSpinner');
    new Chart(document.getElementById('passFailChart'), {
      type: 'doughnut',
      data: {
        labels: ['Passed', 'Failed'],
        datasets: [{
          data: [window.passCount, window.failCount],
          backgroundColor: [chartColors.success, chartColors.danger]
        }]
      },
      options: {
        cutout: '60%',
        responsive: true
      }
    });
  }

  // Course Progress Chart
  if (window.courseLabels) {
    hideSpinner('courseChartSpinner');
    new Chart(document.getElementById('courseChart'), {
      type: 'bar',
      data: {
        labels: window.courseLabels,
        datasets: [{
          label: 'Avg Progress (%)',
          data: window.courseProgress,
          backgroundColor: chartColors.warning
        }]
      },
      options: {
        responsive: true,
        indexAxis: 'y',
        scales: { x: { beginAtZero: true, max: 100 } },
        plugins: { legend: { display: false } },
        onClick: (e, elements) => {
          if(elements.length) {
            const idx = elements[0].index;
            const courseTitle = window.courseLabels[idx];
            window.location.href = `/admin/analytics/course/${encodeURIComponent(courseTitle)}`;
          }
        }
      }
    });
  }

  // Trend Chart
  if (window.trendLabels) {
    hideSpinner('trendChartSpinner');
    new Chart(document.getElementById('trendChart'), {
      type: 'line',
      data: {
        labels: window.trendLabels,
        datasets: [{
          label: 'Avg Score',
          data: window.trendScores,
          fill: false,
          borderColor: chartColors.info,
          tension: 0.2,
          pointBackgroundColor: chartColors.info
        }]
      },
      options: {
        responsive: true,
        scales: { y: { beginAtZero: true, max: 100 } }
      }
    });
  }
});

// Export button
function exportData(what, fmt) {
  window.open(`/admin/analytics/export/${what}/${fmt}`, '_blank');
}