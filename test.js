const puppeteer = require('puppeteer');
const chai = require('chai');
const expect = chai.expect;
const config = require("./config.json");

describe('Flask Application Tests', function() {
    let browser;
    let page;

    // Increase the default timeout for each test
    this.timeout(10000);

    before(async function() {
        browser = await puppeteer.launch(config);
        [page] = await browser.pages();
        await page.emulateMediaType("screen");
        await page.setViewport(config.defaultViewport);
        await page.goto(config.url + "/init", {waitUntil: "load", timeout: 0});
    });

    after(async function() {
        if (browser) {
            await browser.close();
        }
    });

    it('should log in successfully', async function() {
        try {
            await page.waitForSelector('#username', {timeout: 5000});
            await page.type('#username', 'bob');
            await page.type('#password', 'bobpass');
            await page.click('input[type="submit"]');
            await page.waitForNavigation({timeout: 10000});
            const loggedIn = await page.evaluate(() => document.querySelector('#logout') !== null);
            expect(loggedIn).to.be.true;
        } catch (error) {
            console.error('Login failed:', error);
            throw error;
        }
    });

    it('should select a Pokémon and verify details', async function() {
        await page.waitForSelector('#poke-54', {timeout: 5000});
        await page.click('#poke-54');
        let details = await Promise.all([
            page.$eval("#pokemon-detail .card-title", e => e.innerText),
            page.$eval("#pokemon-detail p:nth-child(2)", e => e.innerText),
            page.$eval("#pokemon-detail p:nth-child(3)", e => e.innerText),
            page.$eval("#pokemon-detail p:nth-child(4)", e => e.innerText),
            page.$eval("#pokemon-detail p:nth-child(5)", e => e.innerText),
            page.$eval("#pokemon-detail img", e => e.getAttribute("src"))
        ]);

        let [name, type1, type2, weight, height, image] = details;
        expect(name).to.not.be.empty;
        expect(type1).to.not.be.empty;
        expect(weight).to.not.be.empty;
        expect(height).to.not.be.empty;
        expect(image).to.not.be.empty;
    });

    it('should capture a Pokémon and verify it is in the list', async function() {
        const pokemonName = 'Duke';
        await page.waitForSelector('#pokemon_name', {timeout: 5000});
        await page.type('#pokemon_name', pokemonName);
        await page.click('#captureBtn');

        await page.waitForSelector('table tbody', {timeout: 5000});
        const captured_poke = await page.evaluate(async (pokemonName) => {
            const rows = Array.from(document.querySelectorAll('table tbody tr'));
            let names = rows.map(row => row.querySelector('td:nth-child(2)').innerText.trim());
            return names.includes(pokemonName);
        }, pokemonName);

        expect(captured_poke).to.be.true;
    });
});