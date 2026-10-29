// ==UserScript==
// @name          OUO Skipper
// @namespace     ouo-skipper
// @include       *ouo.io/*
// @include       *ouo.press/*
// @include       *hikarinoakari.com/*
// @version       1.0.1
// @author        Soitora
// @description   Skip OUO and HikariNoAkari ads.
// @icon          https://m.media-amazon.com/images/I/617nhPmUb5L.png
// @homepageURL   https://gist.github.com/Soitora/8a9bd6383362a9ddf664704d498ea392
// @downloadURL   https://gist.githubusercontent.com/Soitora/8a9bd6383362a9ddf664704d498ea392/raw/ouo-skipper.js
// @require       https://ajax.googleapis.com/ajax/libs/jquery/3.6.1/jquery.min.js
// @require       https://unpkg.com/xregexp/xregexp-all.js
// @require       https://unpkg.com/loglevel/dist/loglevel.min.js
// ==/UserScript==

function waitForElm(selector) {
	return new Promise((resolve) => {
		if (document.querySelector(selector)) {
			return resolve(document.querySelector(selector));
		}

		const observer = new MutationObserver((mutations) => {
			if (document.querySelector(selector)) {
				resolve(document.querySelector(selector));
				observer.disconnect();
			}
		});

		observer.observe(document.body, {
			childList: true,
			subtree: true,
		});
	});
}

$.fn.classChange = function (cb) {
	return $(this).each((_, el) => {
		new MutationObserver((mutations) => {
			mutations.forEach((mutation) => cb && cb(mutation.target, $(mutation.target).prop(mutation.attributeName)));
		}).observe(el, {
			attributes: true,
			attributeFilter: ["class"], // only listen for class attribute changes
		});
	});
};

let captchaButton = $(`#captcha button:contains("I'm a human")`);
captchaButton.classChange((el, newClass) => pressButton("captcha", el));

let getLinkButton = $(`form button:contains("Get Link")`);
getLinkButton.classChange((el, newClass) => pressButton("link", el));

let hikariContinueButton = $(`.boton > h2 > a`);
hikariContinueButton.on("DOMSubtreeModified", function () {
	setTimeout(() => {
		if (hikariContinueButton.text().trim() == "Click here to continue") {
			window.location.replace($(this).attr("href"));
		}
	}, 1000);
});

function pressButton(type, el) {
	console.log(el + " was changed");
	switch (type) {
		case "captcha":
			captchaButton.click();
			break;
		case "link":
			getLinkButton.click();
			break;
		case "hikari":
			hikariContinueButton.click();
			break;
	}
}