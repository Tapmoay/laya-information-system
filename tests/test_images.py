

def test_extract_images_prefers_og_image_and_skips_icons():
    from judgment.images import extract_images

    html = """
    <html><head>
    <meta property="og:image" content="/hero.jpg">
    </head><body>
    <img src="/logo.png">
    <img src="/uploads/photo-1.webp">
    <img data-src="https://cdn.example.com/a/b.png">
    <img src="data:image/gif;base64,AAA">
    </body></html>
    """
    images = extract_images(html, "https://example.com/post/1", limit=3)
    assert images[0] == "https://example.com/hero.jpg"
    assert "https://example.com/uploads/photo-1.webp" in images
    assert all("logo" not in url for url in images)
    assert all(not url.startswith("data:") for url in images)


def test_extract_images_respects_limit_and_keeps_order():
    from judgment.images import extract_images

    html = "".join('<img src="/p%d.jpg">' % i for i in range(6))
    images = extract_images(html, "https://example.com/", limit=2)
    assert images == ["https://example.com/p0.jpg", "https://example.com/p1.jpg"]
