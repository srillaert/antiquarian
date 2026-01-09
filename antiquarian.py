# TODO downsample assets

import binascii
import os
import re
import shutil
import sys
from ConfigParser import RawConfigParser as ConfigParser
from datetime import datetime
from tempfile import gettempdir

from PIL import Image

from lxml import html
from lxml.html import builder

from books import get_books
from request import init_request, request, request_cached, dump, undump, copydump, checkpath

config = None


def normalize_dir():
    script_dir = os.path.dirname(sys.argv[0])
    if script_dir:
        os.chdir(script_dir)


def request_cached_patched(filepath, *openargs):
    cached_filepath = checkpath(config["cachepath"] + "/" + filepath)
    content = request_cached(cached_filepath, *openargs, debug=True)

    # use patches where appropriate
    patched_filepath = checkpath(config["patchpath"] + "/" + filepath)
    if os.path.exists(patched_filepath):
        with open(patched_filepath, "rb") as fin:
            print "using patched file for", filepath
            content = fin.read()
    return content


def get_articles_info(sitemap):
    articles = dict()

    # filter year, month, name from url
    urlinfo_re = re.compile(r"^https://www.filfre.net/(....)/(..)/(.+?)/$")

    root = html.fromstring(sitemap)
    list_elements = root.xpath("//div[@class='entry']/*/ul/li")
    for list_element in list_elements:
        anchor_element = list_element.xpath("a").pop()
        url = anchor_element.get("href")
        title = anchor_element.text.encode("utf-8") \
            .replace("&", "&amp;")

        span_element = list_element.xpath("span").pop()
        date = span_element.text

        (year, month, name) = urlinfo_re.match(url).groups()

        filename = "%s-%s-%s.html" % (year, month, name)

        articles[name] = {
            "name": name,
            "url": url,
            "year": year,
            "month": month,
            "title": title,
            "date": date,
            "filename": filename,
        }

    return articles


def transform_articles(book):
    print "transforming", book["name"], book["title"]

    # prepare directories
    epub_dir = checkpath(config["bookpath"] + "/" + book["name"])
    oebps_dir = checkpath(epub_dir + "/OEBPS")
    meta_dir = checkpath(epub_dir + "/META-INF")
    content_dir = checkpath(oebps_dir + "/content")
    assets_dir = checkpath(oebps_dir + "/assets")
    temp_dir = gettempdir()

    # copy static templates
    copydump("templates/mimetype", epub_dir + "/mimetype")
    copydump("templates/container.xml", meta_dir + "/container.xml")
    copydump("templates/covers/" + book["cover"], assets_dir + "/cover.jpg")
    copydump("templates/style.css", content_dir + "/style.css")

    chapter_template = undump("templates/chapter.xhtml")

    manifest_entries = list()
    spine_entries = list()
    nav_entries = list()
    ncx_entries = list()
    img_ids = list()
    modified_datetime = datetime(1, 1, 1)

    image_pattern = r"^https?://www.filfre.net/wp-content/uploads/(....)/(..)/(.*)$"
    image_re = re.compile(image_pattern)

    year_pattern = r"^\d\d\d\d.*$"
    year_re = re.compile(year_pattern)

    for article in book["articles"]:
        content = request_cached_patched(article["filename"], article["url"])
        root = html.fromstring(content)
        entry_element = root.xpath("//div[@class='entry']").pop()

        # handle img elements
        for img_element in entry_element.xpath("//img"):
            for img_del_attrib in ("fetchpriority", "srcset", "sizes"):
                if img_del_attrib in img_element.attrib:
                    del img_element.attrib[img_del_attrib]

            img_element.attrib["class"] = "centered"

            # redirect and manifest
            img_src = img_element.attrib["src"]
            img_match = image_re.match(img_src)
            if img_match:
                (year, month, filename) = img_match.groups()

                if filename == "original_D+D-300x225.jpg":
                    filename = "original_DnD-300x225.jpg"
                filename = "%s-%s-%s" % (year, month, filename)

                mime_type = None
                filename_lower = filename.lower()
                if filename_lower.endswith(".jpg") or filename_lower.endswith(".jpeg"):
                    mime_type = "image/jpeg"
                elif filename_lower.endswith(".png"):
                    mime_type = "image/png"
                elif filename_lower.endswith(".gif"):
                    mime_type = "image/gif"
                elif filename_lower.endswith(".bmp"):
                    pass
                elif filename_lower.endswith(".webp"):
                    pass
                else:
                    print "unidentified media type for", filename_lower

                # make sure the image is cached
                image = request_cached_patched(filename, img_src)

                # copy to assets
                if filename_lower.endswith(".webp") \
                        or filename_lower.endswith(".bmp"):
                    tempfile = temp_dir + "/" + filename
                    dump(image, tempfile)
                    im = Image.open(tempfile).convert("RGB")

                    filename = filename[:-5] + ".png"
                    im.save(assets_dir + "/" + filename)
                    mime_type = "image/png"
                else:
                    dump(image, assets_dir + "/" + filename)

                img_element.attrib["src"] = "../assets/" + filename

                img_id = binascii.crc32(filename) & 0xffffffff
                if img_id not in img_ids:
                    img_ids.append(img_id)
                    manifest_entries.append(
                        '\t\t<item id="img-%08x" href="assets/%s" media-type="%s"/>'
                        % (img_id, filename, mime_type)
                    )

        # handle audio elements
        for audio_element in entry_element.xpath("//audio"):
            new_element = None
            for source_element in audio_element.xpath("./source"):
                src = source_element.attrib["src"]
                src = src[:src.rfind("?")]
                if src.startswith("/"):
                    src = "https://www.filfre.net" + src
                new_element = builder.P(
                    "Link to audio:",
                    builder.BR,
                    builder.A(src, href=src)
                )
            if new_element is not None:
                parent_element = audio_element.getparent()
                parent_element.remove(audio_element)
                parent_element.append(new_element)

        # handle div elements
        for div_element in entry_element.xpath("//div[@align]"):
            del div_element.attrib["align"]
        for div_element in entry_element.xpath("//div[@style]"):
            del div_element.attrib["style"]

        for div_element in entry_element.xpath("//div[@class='skip-for-ebook']"):
            parent_element = div_element.getparent()
            parent_element.remove(div_element)

        for div_element in entry_element.xpath("//div[@class='wp-video']"):
            new_element = None
            for source_element in div_element.xpath("./video/source"):
                src = source_element.attrib["src"]
                src = src[:src.rfind("?")]
                if src.startswith("/"):
                    src = "https://www.filfre.net" + src
                new_element = builder.P(
                    "Link to video:",
                    builder.BR,
                    builder.A(src, href=src)
                )
            if new_element is not None:
                parent_element = div_element.getparent()
                parent_element.remove(div_element)
                parent_element.append(new_element)

        # handle span elements
        for span_element in entry_element.xpath("//span[@id='spoiler']"):
            text = span_element.text
            if text is None:
                for italic_element in span_element.xpath("./i"):
                    text = italic_element.text
            parent = span_element.getparent()
            parent.remove(span_element)
            if text is not None:
                parent.text = "(Spoiler: " + text + ")"
            else:
                print "error: could not find spoiler text"
                sys.exit(-1)

        # handle paragraph elements
        for paragraph_element in entry_element.xpath("//p[@align]"):
            del paragraph_element.attrib["align"]
        for paragraph_element in entry_element.xpath("//p[@class='audioplayer_container']"):
            paragraph_element.getparent().remove(paragraph_element)

        # handle script elements
        for script_element in entry_element.xpath("//script"):
            parent = script_element.getparent()
            parent.text = "Embedded Javascript removed for eBook."
            parent.remove(script_element)

        # handle iframe elements
        for iframe_element in entry_element.xpath("//iframe"):
            src = iframe_element.attrib["src"]
            parent = iframe_element.getparent()
            parent.remove(iframe_element)

            anchor_element = builder.A("See " + src, href=src)
            parent.append(anchor_element)

        # handle anchor elements
        for anchor_element in entry_element.xpath("//a[@href]"):
            url = anchor_element.attrib["href"]
            if url.startswith("www."):
                url = "https://" + url
            elif url.startswith("ww."):
                url = "https://w" + url
            elif url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = "https://www.filfre.net" + url
            elif year_re.match(url):
                url = "https://www.filfre.net/" + url

            if url.endswith('"'):
                url = url[:-1]

            url = url \
                .replace("^", "%5E") \
                .replace("$", "%24") \
                .replace("{", "%7B") \
                .replace("}", "%7D")
            anchor_element.attrib["href"] = url

        # handle emphasis elements
        # NOTE: this fixes a weird bug in Jimmy's setup where an apostrophe after an emphasis gets reversed
        for em_element in entry_element.xpath("//em"):
            if em_element.tail:
                if em_element.tail.startswith(u"\u2018s"):
                    em_element.tail = em_element.tail[2:]
                    em_element.text = (em_element.text if em_element.text is not None else "") + u"\u2019s"

        # remove scripted parts
        # mbmaplayer duplicate-id bug
        for check_element in entry_element.xpath("//*"):
            if "onclick" in check_element.attrib:
                del check_element.attrib["onclick"]
            if "onkeypress" in check_element.attrib:
                del check_element.attrib["onkeypress"]
            if "id" in check_element.attrib and check_element.attrib["id"].startswith("mbmaplayer_"):
                del check_element.attrib["id"]

        entry_children = entry_element.getchildren()[:-1]
        entry = "".join(map(lambda x: html.tostring(x, method="xml"), entry_children))

        chapter_name = article["name"]
        chapter_id = binascii.crc32(chapter_name) & 0xffffffff
        chapter = chapter_template \
            .replace("{name}", chapter_name) \
            .replace("{title}", article["title"]) \
            .replace("{date}", article["date"]) \
            .replace("{entry}", entry)
        chapter_filename = "%s-%s-%s.xhtml" % (article["year"], article["month"], chapter_name)
        dump(chapter, content_dir + "/" + chapter_filename)

        manifest_entry = '\t\t<item id="xhtml-%08x" href="content/%s" media-type="application/xhtml+xml"/>' \
                         % (chapter_id, chapter_filename)
        manifest_entries.append(manifest_entry)

        spine_entry = '\t\t<itemref idref="xhtml-%08x"/>' % chapter_id
        spine_entries.append(spine_entry)

        nav_entries.append("\t\t\t\t<li>")
        nav_entry = '\t\t\t\t\t<a href="%s">%s</a>' % (chapter_filename, article["title"])
        nav_entries.append(nav_entry)

        ncx_entries.append(
            '\t\t<navPoint id="xhtml-%08x"><navLabel><text>%s</text></navLabel><content src="%s"/></navPoint>'
            % (chapter_id, article["title"], chapter_filename)
        )

        # find last modified_date
        modified_datetime = max(modified_datetime, datetime(int(article["year"]), int(article["month"]), 1))

        # COMMENTS
        if not book["has_comments"]:
            nav_entries.append("\t\t\t\t</li>")
            continue

        comments_parts = root.get_element_by_id("comments")
        try:
            comments_title = comments_parts.get_element_by_id("comments-title")
            comments_list = comments_parts.xpath("ol[@class='commentlist']").pop()
            elements = \
                comments_list.xpath("//div[@class='reply']") \
                + comments_list.xpath("//div[@class='cl']") \
                + comments_list.xpath("//strike")
            for element in elements:
                element.getparent().remove(element)
            comments_list.tag = "ul"

            chapter_name = article["name"] + "-comments"
            chapter_id = binascii.crc32(chapter_name) & 0xffffffff
            chapter_title = "Comments"
            chapter_subtitle = comments_title.text[:-3]
            chapter = chapter_template \
                .replace("{name}", chapter_name) \
                .replace("{title}", chapter_title) \
                .replace("{date}", chapter_subtitle) \
                .replace("{entry}", html.tostring(comments_list, method="xml"))
            chapter_filename = "%s-%s-%s.xhtml" % (article["year"], article["month"], chapter_name)
            dump(chapter, content_dir + "/" + chapter_filename)

            manifest_entry = '\t\t<item id="xhtml-%08x" href="content/%s" media-type="application/xhtml+xml"/>' \
                             % (chapter_id, chapter_filename)
            manifest_entries.append(manifest_entry)

            spine_entry = '\t\t<itemref idref="xhtml-%08x"/>' % chapter_id
            spine_entries.append(spine_entry)

            nav_entry = '\t\t\t\t\t<ol hidden="hidden"><li><a href="%s">%s</a></li></ol>' \
                        % (chapter_filename, chapter_title)
            nav_entries.append(nav_entry)
            nav_entries.append("\t\t\t\t</li>")

        except KeyError:
            nav_entries.append("\t\t\t\t</li>")
            print "article has no comments", article["name"]

    template = undump("templates/content.opf") \
        .replace("{book-name}", book["name"]) \
        .replace("{book-title}", book["title"]) \
        .replace("{book-description}", book["description"]) \
        .replace("{modified}", modified_datetime.isoformat()) \
        .replace("{manifest-entries}", "\n".join(manifest_entries)) \
        .replace("{spine-entries}", "\n".join(spine_entries))
    dump(template, oebps_dir + "/content.opf")

    now = datetime.now()
    day = now.day
    daysuffix = "th"
    if (day <= 10 or day >= 20) and day % 10 == 1:
        daysuffix = "st"
    elif (day <= 10 or day >= 20) and day % 10 == 2:
        daysuffix = "nd"
    elif (day <= 10 or day >= 20) and day % 10 == 3:
        daysuffix = "rd"

    template = undump("templates/titlepage.xhtml") \
        .replace("{book-title}", book["title"].replace(", ", "<br/>")) \
        .replace("{book-description}", book["description"]) \
        .replace("{book-date}", now.strftime("%B " + str(now.day) + daysuffix + ", %Y"))
    dump(template, content_dir + "/titlepage.xhtml")

    template = undump("templates/nav.xhtml") \
        .replace("{nav-entries}", "\n".join(nav_entries))
    dump(template, content_dir + "/nav.xhtml")

    template = undump("templates/legacy-nav.ncx") \
        .replace("{book-name}", book["name"]) \
        .replace("{book-title}", book["title"]) \
        .replace("{ncx-entries}", "\n".join(ncx_entries))
    dump(template, content_dir + "/legacy-nav.ncx")


def init_config(filepath):
    global config

    config_parser = ConfigParser(allow_no_value=True)
    config_parser.readfp(open(filepath))

    config = dict()
    for (x, y) in config_parser.items("general"):
        config[x] = y
    for (x, y) in config_parser.items(sys.platform):
        config[x] = y

    checkpath(config["bookpath"])
    checkpath(config["cachepath"])
    checkpath(config["patchpath"])


def compile_book(book):
    # compile bookpath to epub-file
    epub_dir = checkpath(config["bookpath"] + "/" + book["name"])
    epub_file = checkpath(config["bookpath"] + "/" + book["name"] + ".epub")
    epub_error_file = checkpath(config["bookpath"] + "/" + book["name"] + "-error.epub.txt")
    cwd = os.getcwd()
    os.chdir(config["bookpath"])

    if not os.path.exists(epub_file):
        cmd = " ".join(
            (
                config["epubcheck"],
                epub_dir,
                "--mode exp --save",
                "2>",
                epub_error_file
            )
        )
        errorcode = os.system(cmd)
        if errorcode != 0:
            print "errors found... see", epub_error_file
            with open(epub_error_file, "r") as fin:
                print fin.read()
            # during actual epubcheck bugs, the file will get written despite errors!
            if os.path.exists(epub_file):
                os.remove(epub_file)
            sys.exit(-1)
        os.remove(epub_error_file)

    os.chdir(cwd)


def main():
    normalize_dir()
    init_request()
    init_config("antiquarian.ini")

    #    sitemap = request_cached(config["cachepath"] + "/sitemap.html", "https://www.filfre.net/sitemap/", debug=True)
    sitemap = request("https://www.filfre.net/sitemap/", debug=True)

    articles_info = get_articles_info(sitemap)
    for book in get_books(articles_info, int(config["volumes_min"]), int(config["volumes_max"]),
                          bool(config["additional_books"])):
        transform_articles(book)
        compile_book(book)


if __name__ == "__main__":
    main()
