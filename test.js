const testsuite3 = require('testsuite3');

// const config = require("./config.json");
// const puppeteer = require('puppeteer');

// async function initialize(){
//     const browser = await puppeteer.launch(config);
//     const [page] = await browser.pages();
//     await page.emulateMediaType("screen");
//     await page.setViewport(config.defaultViewport);
//     await page.goto(config.url+"/init", {waitUntil: "load", timeout: 0});
//     return {browser, page};
// }

// async function login(page){
//     await page.type('#username', 'bob');
//     await page.type('#password', 'bobpass');
//     await page.click('input[type="submit"]');
//     return page;
// }

// async function selectPokemon(page, id){
//     await page.click(`#poke-${id}`);
//     let details = await Promise.all([
//         page.$eval("#pokemon-detail .card-title", e => e.innerText),
//         page.$eval("#pokemon-detail p:nth-child(2)", e => e.innerText),
//         page.$eval("#pokemon-detail p:nth-child(3)", e => e.innerText),
//         page.$eval("#pokemon-detail p:nth-child(4)", e => e.innerText),
//         page.$eval("#pokemon-detail p:nth-child(5)", e => e.innerText),
//         page.$eval("#pokemon-detail img", e => e.getAttribute("src"))
//     ]);

//     let [name, type1, type2, weight, height, image] = details;
//     return {name, type1, type2, weight, height, image};
// }

// async function capturePokemon(page, name){

//     await page.type('#pokemon_name', name);
//     await page.click('#captureBtn');
//     try{
//         const new_poke = await page.evaluate(async (name) => {
      
//             const rows = Array.from(document.querySelectorAll('table tbody tr')); // Select all the rows in the table
//             let pokemon = [];
//             let names = [];

//             for (let i = 0; i < rows.length; i++) {
//               const cells = rows[i].querySelectorAll('td'); // Get all cells of the row
//               if (cells.length > 1) { // Make sure the row has at least two columns
//                 pokemon.push(cells[0].textContent.trim()); // Get text from the first column
//                 names.push(cells[1].textContent.trim()); // Get text from the second column
//               }
//             }

//             const cell_rename = document.querySelector(`#rename-${name}`);
//             const cell_release = document.querySelector(`#release-${name}`);
    
//             return {pokemon, names, cell_rename, cell_release};
//         }, name);

//         return new_poke;
//     }catch(e){
//         console.error(e);
//         return {
//             pokemon:[], 
//             names:[], 
//             cell_rename:null, 
//             cell_release: null
//         }
//     }

// }

// async function main(){
//     const {browser, page} = await initialize();
//     try {
//         await login(page);
//         let selectedPokemon = await selectPokemon(page, 54);
//         let captured_poke = await capturePokemon(page, "Duke");
//         await browser.close();
//     }
//     catch(e){
//         console.error(e);
//     }finally{
//         console.log('done');
//         if(browser) await browser.close();
//     }
// }

// main();

