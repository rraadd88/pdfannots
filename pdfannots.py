#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function
import sys, io, textwrap
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.layout import LAParams, LTContainer, LTAnno, LTText, LTTextBox
from pdfminer.converter import TextConverter
from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument, PDFNoOutlines
from pdfminer.psparser import PSLiteralTable, PSLiteral
import pdfminer.pdftypes as pdftypes
# import pdfminer.settings

# pdfminer.settings.STRICT = False
import re

SUBSTITUTIONS = {
    u'ﬀ': 'ff',
    u'ﬁ': 'fi',
    u'ﬂ': 'fl',
    u'’': "'",
}

ANNOT_SUBTYPES = set(['Text', 'Highlight', 'Squiggly', 'StrikeOut', 'Underline'])

DEBUG_BOXHIT = False

def boxhit(item, box):
    (x0, y0, x1, y1) = box
    assert item.x0 <= item.x1 and item.y0 <= item.y1
    assert x0 <= x1 and y0 <= y1

    # does most of the item area overlap the box?
    # http://math.stackexchange.com/questions/99565/simplest-way-to-calculate-the-intersect-area-of-two-rectangles
    x_overlap = max(0, min(item.x1, x1) - max(item.x0, x0))
    y_overlap = max(0, min(item.y1, y1) - max(item.y0, y0))
    overlap_area = x_overlap * y_overlap
    item_area = (item.x1 - item.x0) * (item.y1 - item.y0)
    assert overlap_area <= item_area

    if DEBUG_BOXHIT and overlap_area != 0:
        print("'%s' %f-%f,%f-%f in %f-%f,%f-%f %2.0f%%" %
              (item.get_text(), item.x0, item.x1, item.y0, item.y1, x0, x1, y0, y1,
               100 * overlap_area / item_area))

    if item_area == 0:
        return False
    else:
        return overlap_area >= 0.5 * item_area

class RectExtractor(TextConverter):
    def __init__(self, rsrcmgr, codec='utf-8', pageno=1, laparams=None):
        dummy = io.StringIO()
        TextConverter.__init__(self, rsrcmgr, outfp=dummy, codec=codec, pageno=pageno, laparams=laparams)
        self.annots = []
        self._lasthit = []

    def setcoords(self, annots):
        self.annots = [a for a in annots if a.boxes]
        self._lasthit = []

    def testboxes(self, item):
        self._lasthit = []
        for a in self.annots:
            if any([boxhit(item, b) for b in a.boxes]):
                self._lasthit.append(a)
        return self._lasthit

    def receive_layout(self, ltpage):
        def render(item):
            if isinstance(item, LTContainer):
                for child in item:
                    render(child)
            elif isinstance(item, LTAnno):
                # this catches whitespace
                for a in self._lasthit:
                    a.capture(item.get_text())
            elif isinstance(item, LTText):
                for a in self.testboxes(item):
                    a.capture(item.get_text())
            if isinstance(item, LTTextBox):
                for a in self.testboxes(item):
                    a.capture('\n')

        render(ltpage)

class Annotation:
    def __init__(self, pageno, tagname, coords=None, rect=None, contents=None):
        self.pageno = pageno
        self.tagname = tagname
        if contents == '':
            self.contents = None
        else:
            self.contents = contents
        self.rect = rect
        self.text = ''

        if coords is None:
            self.boxes = None
        else:
            assert len(coords) % 8 == 0
            self.boxes = []
            while coords != []:
                (x0,y0,x1,y1,x2,y2,x3,y3) = coords[:8]
                coords = coords[8:]
                xvals = [x0, x1, x2, x3]
                yvals = [y0, y1, y2, y3]
                box = (min(xvals), min(yvals), max(xvals), max(yvals))
                self.boxes.append(box)

    def capture(self, text):
        if text == '\n':
            # kludge for latex: elide hyphens, join lines
            if self.text.endswith('-'):
                self.text = self.text[:-1]
            else:
                self.text += ' '
        else:
            self.text += text

    def gettext(self):
        if self.text:
            # replace tex ligatures (and other common odd characters)
            return ''.join([SUBSTITUTIONS.get(c, c) for c in self.text.strip()])
        else:
            return None

    def getstartpos(self):
        if self.rect:
            (x0, y0, x1, y1) = self.rect
        elif self.boxes:
            (x0, y0, x1, y1) = self.boxes[0]
        else:
            return None
        return (min(x0, x1), max(y0, y1)) # assume left-to-right top-to-bottom text :)

def getannots(pdfannots, pageno, linesperpage=20,pagew=815,pageh=600):
    annots = []
    linenos=''
    for pa in pdfannots:
        subtype = pa.get('Subtype')
        if subtype.name=='Highlight':
            # print(subtype.name)
            if subtype is not None and subtype.name not in ANNOT_SUBTYPES:
                continue

            contents = pa.get('Contents')
            if contents is not None:
                contents = str(contents).decode('iso8859-15')#'iso8859-15') #'utf-8'            
                contents = contents.replace('\r\n', '\n').replace('\r', '\n')
                contents = contents[2:]
                # print(contents.decode('iso8859-15'))
                # contents = contents.replace('þÿ', '')
                # contents = re.sub(r'[^\w]',' ',contents)
            # print(pa['Rect']) # xmin ymin xmax ymax
            xmin, ymin, xmax, ymax = pa['Rect']
            lineini=int((1-ymax/float(pagew))*20)
            lineend=int((1-ymin/float(pagew))*20)
            linenos="line {} to {}".format(lineini, lineend)
            # print(linenos,ymin,ymax)
            a = Annotation(pageno, subtype.name.lower(), pa.get('QuadPoints'), pa.get('Rect'), contents)
            annots.append(a)
    # if linenos=='':
    #     print(subtype.name)
    #     print(pa['Rect']) # xmin ymin xmax ymax        
    return annots,linenos

def normalise_to_box(pos, box):
    (x, y) = pos
    (x0, y0, x1, y1) = box
    if x < x0:
        x = x0
    elif x > x1:
        x = x1
    if y < y0:
        y = y0
    elif y > y1:
        y = y1
    return (x, y)

def nearest_outline(outlines, pageno, mediabox, pos):
    (x, y) = normalise_to_box(pos, mediabox)
    prev = None
    for o in outlines:
        if o.pageno < pageno:
            prev = o
        elif o.pageno > pageno:
            return prev
        else:
            # XXX: assume two-column left-to-right top-to-bottom documents
            (ox, oy) = normalise_to_box((o.x, o.y), mediabox)
            (x0, y0, x1, y1) = mediabox
            colwidth = (x1 - x0) / 2
            outline_col = (ox - x0) // colwidth
            pos_col = (x - x0) // colwidth
            if outline_col > pos_col or (outline_col == pos_col and o.y < y):
                return prev
            else:
                prev = o
    return prev


def prettyprint(annots, outlines, mediaboxes,alllinenos):

    tw = textwrap.TextWrapper(width=80, initial_indent=" * ", subsequent_indent="   ")

    def fmtpos(annot):
        apos = annot.getstartpos()
        if apos:
            o = nearest_outline(outlines, annot.pageno, mediaboxes[annot.pageno], apos)
        else:
            o = None
        if o:
            return "Page %d (%s):" % (annot.pageno + 1, o.title)
        else:
            return "Page %d:" % (annot.pageno + 1)

    def fmttext(annot):
        if annot.boxes:
            if annot.gettext():
                return '"%s"' % annot.gettext()
            else:
                return "(XXX: missing text!)"
        else:
            return ''

    def formatitem(*args):
        msg = '- '+'\n'.join(args)
        # print(tw.fill(msg) + "\n")
        return msg + "\n"
    def printitem(*args):
        print(formatitem(*args))

    nits = [a for a in annots if a.tagname in ['squiggly', 'strikeout', 'underline']]
    comments = [a for a in annots if a.tagname in ['highlight'] and a.contents]
    # commentslinenos = [alllinenos[ai] for ai,a in enumerate(annots) if a.tagname in ['highlight', 'text'] and a.contents]    
    highlights = [a for a in annots if a.tagname == 'highlight' and a.contents is None]
    # print(alllinenos)
    allcomments=[]
    if comments:
        # if highlights:
        #     print() # blank
        # print("# All comments\n")
        commenti=0
        for a in comments:
            text = fmttext(a)
            if text:
                # XXX: lowercase the first word, to join it to the "Regarding" sentence
                contents = a.contents
                firstword = contents.split()[0]
                if firstword != 'I' and not firstword.startswith("I'"):
                    contents = contents[0].lower() + contents[1:]
                # printitem(fmtpos(a), alllinenos[commenti] if commenti<len(alllinenos) else '', "Regarding", text + ",", contents)
                # printitem(fmtpos(a), "Regarding "+text, contents)
                allcomments.append(formatitem(fmtpos(a), "Regarding "+text, contents))
                commenti+=1
            # else:
            #     printitem(fmtpos(a), a.contents)
    hashtag2category={'#major':'# Major comments:\n',
    '#minor':'# Minor comments:\n',
    '#checkinabstract': '#checkinabstract\n',
    '#checkinresults':'#checkinresults\n',
    '#checkindiscussion':'#checkindiscussion\n',
    '#checkinmethods':'#checkinmethods\n',
    '#checkinrefs':'#checkinrefs\n',
    '#checkout':'#checkout\n',}
    for ctype in ['#major','#minor']:
        print(hashtag2category[ctype]) 
        for c in allcomments:
            if not '#check' in c:
                if ctype in c:
                    print(c.replace(ctype,'')) 

    for ctype in [k for k in hashtag2category.keys() if '#check' in k]:
        ci=0
        for c in allcomments:
            if '#check' in c:
                if ci==0:
                    print(hashtag2category[ctype]) 
                print(c)
                ci+=1

    ci=0
    for c in allcomments:
        if not '#' in c:
            if ci==0:
                print('# Not hashtagged comments:') 
            print(c)
            ci+=1

    if highlights:
        print("## Just Highlights\n")
        for a in highlights:
            printitem(fmtpos(a), fmttext(a))

    if nits:
        if highlights or comments:
            print() #  blank
        print("## Nits\n")
        for a in nits:
            text = fmttext(a)
            if a.contents:
                printitem(fmtpos(a), "%s -> %s" % (text, a.contents))
            else:
                printitem(fmtpos(a), "%s" % text)

def resolve_dest(doc, dest):
    if isinstance(dest, bytes):
        dest = pdftypes.resolve1(doc.get_dest(dest))
    elif isinstance(dest, PSLiteral):
        dest = pdftypes.resolve1(doc.get_dest(dest.name))
    if isinstance(dest, dict):
        dest = dest['D']
    return dest

class Outline:
    def __init__(self, title, dest, pageno, x, y):
        self.title = title
        self.dest = dest
        self.pageno = pageno
        self.x = x
        self.y = y

def get_outlines(doc, pagesdict):
    result = []
    for (_, title, destname, actionref, _) in doc.get_outlines():
        if destname is None and actionref:
            action = actionref.resolve()
            if isinstance(action, dict):
                subtype = action.get('S')
                if subtype is PSLiteralTable.intern('GoTo'):
                    destname = action.get('D')
        if destname is None:
            continue
        dest = resolve_dest(doc, destname)
        pageno = pagesdict[dest[0].objid]
        (_, _, targetx, targety, _) = dest
        result.append(Outline(title, destname, pageno, targetx, targety))
    return result

def printannots(fh):
    rsrcmgr = PDFResourceManager()
    laparams = LAParams()
    device = RectExtractor(rsrcmgr, laparams=laparams)
    interpreter = PDFPageInterpreter(rsrcmgr, device)
    parser = PDFParser(fh)
    doc = PDFDocument(parser)

    pagesdict = {}
    mediaboxes = {}
    allannots = []
    alllinenos=[]
    for (pageno, page) in enumerate(PDFPage.create_pages(doc)):
        pagesdict[page.pageid] = pageno
        mediaboxes[pageno] = page.mediabox
        if page.annots is None or page.annots == []:
            continue

        # emit progress indicator
        sys.stderr.write((" " if pageno > 0 else "") + "%d" % (pageno + 1))
        sys.stderr.flush()

        pdfannots = []
        for a in pdftypes.resolve1(page.annots):
            if isinstance(a, pdftypes.PDFObjRef):
                # print(a.resolve())
                pdfannots.append(a.resolve())
            else:
                sys.stderr.write('Warning: unknown annotation: %s\n' % a)

        pageannots,linenos = getannots(pdfannots, pageno)
        alllinenos.append(linenos)
        device.setcoords(pageannots)
        interpreter.process_page(page)
        # print(pageannots)
        allannots.extend(pageannots)

    sys.stderr.write("\n")

    outlines = []
    try:
        outlines = get_outlines(doc, pagesdict)
    except PDFNoOutlines:
        sys.stderr.write("Document doesn't include outlines (\"bookmarks\")\n\n\n")
    except:
        e = sys.exc_info()[0]
        sys.stderr.write("Warning: failed to retrieve outlines: %s\n\n\n" % e)

    device.close()

    prettyprint(allannots, outlines, mediaboxes,alllinenos)

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: %s FILE.PDF\n" % sys.argv[0])
        sys.exit(1)

    try:
        fh = open(sys.argv[1], 'rb')
    except OSError as e:
        sys.stderr.write("Error: %s\n" % e)
        sys.exit(1)
    else:
        with fh:
            printannots(fh)

if __name__ == "__main__":
    main()
