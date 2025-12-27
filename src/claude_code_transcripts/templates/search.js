(function() {
    var totalPages = {{ total_pages }};
    var searchBox = document.getElementById('search-box');
    var searchInput = document.getElementById('search-input');
    var searchBtn = document.getElementById('search-btn');
    var modal = document.getElementById('search-modal');
    var modalInput = document.getElementById('modal-search-input');
    var modalSearchBtn = document.getElementById('modal-search-btn');
    var modalCloseBtn = document.getElementById('modal-close-btn');
    var searchStatus = document.getElementById('search-status');
    var searchResults = document.getElementById('search-results');

    if (!searchBox || !modal) return;

    // Show search box (progressive enhancement)
    searchBox.style.display = 'flex';

    // Gist preview support - detect if we're on gistpreview.github.io
    var isGistPreview = window.location.hostname === 'gistpreview.github.io';
    var gistId = null;
    var gistOwner = null;
    var gistInfoLoaded = false;

    if (isGistPreview) {
        // Extract gist ID from URL path like /gist-id/index.html or /gist-id/raw/index.html
        var pathMatch = window.location.pathname.match(/^\/([a-f0-9]+)/i);
        if (pathMatch) {
            gistId = pathMatch[1];
        }
    }

    async function loadGistInfo() {
        if (!isGistPreview || !gistId || gistInfoLoaded) return;
        try {
            var response = await fetch('https://api.github.com/gists/' + gistId);
            if (response.ok) {
                var info = await response.json();
                gistOwner = info.owner.login;
                gistInfoLoaded = true;
            }
        } catch (e) {
            console.error('Failed to load gist info:', e);
        }
    }

    function getPageUrl(pageFile) {
        if (isGistPreview && gistOwner && gistId) {
            // Use raw gist URL for fetching
            return 'https://gist.githubusercontent.com/' + gistOwner + '/' + gistId + '/raw/' + pageFile;
        }
        return pageFile;
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function escapeRegex(string) {
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function openModal(query) {
        modalInput.value = query || '';
        searchResults.innerHTML = '';
        searchStatus.textContent = '';
        modal.showModal();
        modalInput.focus();
        if (query) {
            performSearch(query);
        }
    }

    function closeModal() {
        modal.close();
        // Update URL to remove search fragment
        if (window.location.hash.startsWith('#search=')) {
            history.replaceState(null, '', window.location.pathname);
        }
    }

    function updateUrlHash(query) {
        if (query) {
            history.replaceState(null, '', '#search=' + encodeURIComponent(query));
        }
    }

    function highlightTextNodes(element, searchTerm) {
        var walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
        var nodesToReplace = [];

        while (walker.nextNode()) {
            var node = walker.currentNode;
            if (node.nodeValue.toLowerCase().indexOf(searchTerm.toLowerCase()) !== -1) {
                nodesToReplace.push(node);
            }
        }

        nodesToReplace.forEach(function(node) {
            var text = node.nodeValue;
            var regex = new RegExp('(' + escapeRegex(searchTerm) + ')', 'gi');
            var parts = text.split(regex);
            if (parts.length > 1) {
                var span = document.createElement('span');
                parts.forEach(function(part) {
                    if (part.toLowerCase() === searchTerm.toLowerCase()) {
                        var mark = document.createElement('mark');
                        mark.textContent = part;
                        span.appendChild(mark);
                    } else {
                        span.appendChild(document.createTextNode(part));
                    }
                });
                node.parentNode.replaceChild(span, node);
            }
        });
    }

    function fixInternalLinks(element, pageFile) {
        // Update all internal anchor links to include the page file
        var links = element.querySelectorAll('a[href^="#"]');
        links.forEach(function(link) {
            var href = link.getAttribute('href');
            link.setAttribute('href', pageFile + href);
        });
    }

    function processPage(pageFile, html, query) {
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        var resultsFromPage = 0;

        // Find all message blocks
        var messages = doc.querySelectorAll('.message');
        messages.forEach(function(msg) {
            var text = msg.textContent || '';
            if (text.toLowerCase().indexOf(query.toLowerCase()) !== -1) {
                resultsFromPage++;

                // Get the message ID for linking
                var msgId = msg.id || '';
                var link = pageFile + (msgId ? '#' + msgId : '');

                // Clone the message HTML and highlight matches
                var clone = msg.cloneNode(true);
                // Fix internal links to include the page file
                fixInternalLinks(clone, pageFile);
                highlightTextNodes(clone, query);

                var resultDiv = document.createElement('div');
                resultDiv.className = 'search-result';
                resultDiv.innerHTML = '<a href="' + link + '">' +
                    '<div class="search-result-page">' + escapeHtml(pageFile) + '</div>' +
                    '<div class="search-result-content">' + clone.innerHTML + '</div>' +
                    '</a>';
                searchResults.appendChild(resultDiv);
            }
        });

        return resultsFromPage;
    }

    async function performSearch(query) {
        if (!query.trim()) {
            searchStatus.textContent = 'Enter a search term';
            return;
        }

        updateUrlHash(query);
        searchResults.innerHTML = '';
        searchStatus.textContent = 'Searching...';

        // Load gist info if on gistpreview (needed for constructing URLs)
        if (isGistPreview && !gistInfoLoaded) {
            searchStatus.textContent = 'Loading gist info...';
            await loadGistInfo();
            if (!gistOwner) {
                searchStatus.textContent = 'Failed to load gist info. Search unavailable.';
                return;
            }
        }

        var resultsFound = 0;
        var pagesSearched = 0;

        // Build list of pages to fetch
        var pagesToFetch = [];
        for (var i = 1; i <= totalPages; i++) {
            pagesToFetch.push('page-' + String(i).padStart(3, '0') + '.html');
        }

        searchStatus.textContent = 'Searching...';

        // Process pages in batches of 3, but show results immediately as each completes
        var batchSize = 3;
        for (var i = 0; i < pagesToFetch.length; i += batchSize) {
            var batch = pagesToFetch.slice(i, i + batchSize);

            // Create promises that process results immediately when each fetch completes
            var promises = batch.map(function(pageFile) {
                return fetch(getPageUrl(pageFile))
                    .then(function(response) {
                        if (!response.ok) throw new Error('Failed to fetch');
                        return response.text();
                    })
                    .then(function(html) {
                        // Process and display results immediately
                        var count = processPage(pageFile, html, query);
                        resultsFound += count;
                        pagesSearched++;
                        searchStatus.textContent = 'Found ' + resultsFound + ' result(s) in ' + pagesSearched + '/' + totalPages + ' pages...';
                    })
                    .catch(function() {
                        pagesSearched++;
                        searchStatus.textContent = 'Found ' + resultsFound + ' result(s) in ' + pagesSearched + '/' + totalPages + ' pages...';
                    });
            });

            // Wait for this batch to complete before starting the next
            await Promise.all(promises);
        }

        searchStatus.textContent = 'Found ' + resultsFound + ' result(s) in ' + totalPages + ' pages';
    }

    // Event listeners
    searchBtn.addEventListener('click', function() {
        openModal(searchInput.value);
    });

    searchInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            openModal(searchInput.value);
        }
    });

    modalSearchBtn.addEventListener('click', function() {
        performSearch(modalInput.value);
    });

    modalInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            performSearch(modalInput.value);
        }
    });

    modalCloseBtn.addEventListener('click', closeModal);

    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            closeModal();
        }
    });

    // Check for #search= in URL on page load
    if (window.location.hash.startsWith('#search=')) {
        var query = decodeURIComponent(window.location.hash.substring(8));
        if (query) {
            searchInput.value = query;
            openModal(query);
        }
    }
})();
