/* Types pie chart — extracted from pokemon_piechart.html in T21.
 *
 * Carried over unchanged apart from reading its data from a JSON island.
 * Chart.js was never loaded on this page, so `new Chart(...)` threw
 * "ReferenceError: Chart is not defined" and the canvas stayed blank. The
 * template now loads the library ahead of this file.
 */

/* global Chart, readPageData */

const chartData = readPageData('piechart-data') || { labels: [], values: [] };

// Create chart
const ctx = document.getElementById('pokemonPieChart').getContext('2d');
const pokemonPieChart = new Chart(ctx, {
    type: 'pie',
    data: {
        labels: chartData.labels,
        datasets: [{
            label: 'Pokémon Types',
            data: chartData.values,
            backgroundColor: ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0'],
        }]
    },
    options: {
        responsive: true
    }
});
