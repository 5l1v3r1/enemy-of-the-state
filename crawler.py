#!/usr/bin/env python

from __future__ import with_statement

from collections import defaultdict
import struct
import logging
import urlparse
import copy
import pydot

import config

class Anchor:
    def __init__(self, url, nvisits=0, target=None):
        self.url = url
        self.nvisits = nvisits
        self.target = target
        self.ignore = False
        self.history = None

    def __repr__(self):
        return 'Anchor(url=%r, nvisits=%d, target=%r)' \
                % (self.url, self.nvisits, self.target)

    def hashData(self):
        return self.url

    def strippedHashData(self):
        return self.url.split('?')[0]

class Form:
    def __init__(self, method, action, inputs=[], textarea=[],
            selects=[]):
        self.method = method.lower()
        self.action = action
        self.inputs = inputs
        self.textarea = textarea
        self.selects = selects
        self.target = None
        self.nvisits = 0
        self.ignore = False
        self.history = None

    def __repr__(self):
        return ('Form(method=%r, action=%r, inputs=%r, textarea=%r,' +
                    'selects=%r)') % (self.method, self.action, self.inputs,
                            self.textarea, self.selects)

    def hashData(self):
        return '(%s,%s,%s,%s)' % (self.action, self.inputs, self.textarea,
                self.selects)

    def getFormKeys(self):
        return self.inputs + self.textarea + self.selects

class Links:
    ANCHOR = 0
    FORM = 1
    def __init__(self, anchors, forms):
        self.anchors = anchors
        self.forms = forms

    def nAnchors(self):
        return len(self.anchors)

    def nForms(self):
        return len(self.forms)

    def len(self, what):
        if what == Links.ANCHOR:
            return self.nAnchors()
        elif what == Links.FORM:
            return self.nForms()
        else:
            raise KeyError(idx)

    def __repr__(self):
        return '(%s, %s)' % (self.anchors, self.forms)

    def hashData(self):
        return '([%s], [%s])' % (','.join(i.hashData() for i in self.anchors),
                ','.join(i.hashData() for i in self.forms))

    def strippedHashData(self):
        return '([%s], [%s])' % (','.join(i.strippedHashData()
                    for i in self.anchors),
                ','.join(i.hashData() for i in self.forms))

    def __getitem__(self, idx):
        if idx[0] == Links.ANCHOR:
            return self.anchors[idx[1]]
        elif idx[0] == Links.FORM:
            return self.forms[idx[1]]
        else:
            raise KeyError(idx)

    def __iter__(self):
        return self.iter(Links.FORM)

    def getUnvisited(self, what=FORM):
        for i,l in enumerate(self.anchors):
            if not l.target and not l.ignore:
                return (Links.ANCHOR, i)
        if what >= Links.FORM:
            for i,l in enumerate(self.forms):
                if not l.target and not l.ignore:
                    return (Links.FORM, i)

    def iter(self, what=FORM):
        if what == Links.ANCHOR:
            iterlist = [self.anchors]
        elif what == Links.FORM:
            iterlist = [self.anchors, self.forms]
        else:
            raise KeyError(idx)

        for i in iterlist:
            for j in i:
                if not j.ignore:
                    yield j

    def enumerate(self):
        for i,k in enumerate(self.anchors):
            if not k.ignore:
                yield ((Links.ANCHOR, i), k)
        for i,k in enumerate(self.forms):
            if not k.ignore:
                yield ((Links.FORM, i), k)

    def clone(self):
        cloned = copy.copy(self)
        cloned.anchors = [copy.copy(i) for i in self.anchors]
        cloned.forms = [copy.copy(i) for i in self.forms]
        for l in cloned.anchors + cloned.forms:
            l.nvisits = 0
        return cloned

class Unvisited:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.anchors = set()
        self.forms = set()

    def addPage(self, page):
        self.anchors.update((page, i) for i in range(page.links.nAnchors()))
        self.forms.update((page, i) for i in range(page.links.nForms()))

    def remove(self, page, link):
        if link[0] == Links.ANCHOR:
            self.anchors.remove((page, link[1]))
        if link[0] == Links.FORM:
            self.forms.remove((page, link[1]))
        else:
            raise KeyError(link)

    def __nonzero__(self):
        return True if self.anchors or self.forms else False

    def logInfo(self):
        self.logger.info("still unvisited: %d anchors, %d forms",
                len(self.anchors), len(self.forms))
        #self.logger.debug("still unvisited: %r anchors, %r forms",
        #        self.anchors, self.forms)

    def len(self, what):
        if what == Links.ANCHOR:
            return len(self.anchors)
        elif what == Links.FORM:
            return len(self.forms)
        else:
            raise KeyError(idx)

    def __contains__(self, link):
        if link[1][0] == Links.ANCHOR:
            return (link[0], link[1][1]) in self.anchors
        if link[1][0] == Links.FORM:
            return (link[0], link[1][1]) in self.forms
        return False

    def __repr__(self):
        return "Unvisited(%r, %r)" % (self.anchors, self.forms)


class PageMapper:
    NOT_AGGREG = 0
    AGGREGATED = 1
    AGGREG_PENDING = 2
    AGGREG_IMPOSS = 3
    STATUS_SPLIT = 4

    class Inner:
        def __init__(self, page):
            self.pages = {page: page}
            self.merged = None
            # we ant to use the first reached page as reference for all the
            # similar pages
            self.original = page
            self.aggregation = PageMapper.NOT_AGGREG

        def __getitem__(self, page):
            return self.pages[page]

        def __setitem__(self, page, samepage):
            self.pages[page] = samepage

        def clear(self):
            self.pages = {}

        def __len__(self):
            return len(self.pages)

        def __contains__(self, k):
            return k in self.pages

        def __iter__(self):
            return self.pages.__iter__()

        def iteritems(self):
            return self.pages.iteritems()

        def itervalues(self):
            return self.pages.itervalues()

        def smartiter(self):
            if self.merged:
                for i in [self.merged]:
                    yield i
            else:
                # iter on values, as keys may be ExactPage()
                for i in self.pages.itervalues():
                    yield i

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.unvisited = Unvisited()
        self.unsubmitted = set()
        self.first = {}

    def __getitem__(self, page):
        if page.templetized  not in self.first:
            self.logger.info("new page %s", page.url)
            self.first[page.templetized] = PageMapper.Inner(page)
            self.unvisited.addPage(page)
        else:
            inner = self.first[page.templetized]
            if inner.aggregation == PageMapper.STATUS_SPLIT:
                self.logger.info("known status splitted page %s", page.url)
                assert page == inner.latest, "Impossible to aggregate when status splitting"
                # remeber that __eq__ for Page has been redefined
                page = inner.latest
            elif page in inner:
                if inner.aggregation == PageMapper.AGGREGATED:
                    self.logger.info("known aggregated page %s", page.url)
                    # XXX: may get thr crawler status out-of-sync
                    page = inner.merged
                elif inner.aggregation in \
                        [PageMapper.AGGREG_PENDING, PageMapper.AGGREG_IMPOSS]:
                    self.logger.info("known aggregatable page %s", page.url)
                    # XXX: may get thr crawler status out-of-sync
                    page = inner[page]
                else:
                    self.logger.info("known page %s", page.url)
                    page = inner[page]
            else:
                if inner.aggregation == PageMapper.AGGREGATED:
                    self.logger.info("new aggregated page %s", page.url)
                    inner[page] = page
                    # XXX: may get thr crawler status out-of-sync
                    page = inner.merged
                elif inner.aggregation in \
                        [PageMapper.AGGREG_PENDING, PageMapper.AGGREG_IMPOSS]:
                    self.logger.info("new aggregatable page %s", page.url)
                    inner[page] = page
                    page.aggregation = PageMapper.AGGREG_PENDING
                else:
                    self.logger.info("new similar page %s", page.url)
                    inner[page] = page
                    if len(inner) >= config.SIMILAR_JOIN_THRESHOLD:
                        self.logger.info("aggregatable pages %r",
                                [p.url for p in inner])
                        inner.aggregation = PageMapper.AGGREG_PENDING
                    self.unvisited.addPage(page)
        return page

    def __iter__(self):
        for i in self.first.itervalues():
            for j in i.smartiter():
                yield j

    def checkAggregatable(self, page):
        # TODO: support for backformlinks
        inner = self.first[page.templetized]
        if inner.aggregation != PageMapper.AGGREG_PENDING:
            return
        # make sure we have visited all the links of the first
        # config.SIMILAR_JOIN_THRESHOLD pages
        for p in inner:
            if p.aggregation != PageMapper.AGGREG_PENDING:
                unvisited = p.links.getUnvisited()
                if unvisited:
                    return

        if self.aggregatable(page):
            self.logger.info("aggregating %r", page)
            inner.merged = inner.original
            inner.aggregation = PageMapper.AGGREGATED
            inner.merged.aggregation = PageMapper.AGGREGATED
            for p in inner:
                # update links from other pages to the merged ones
                for pred, anchor in p.backlinks:
                    assert pred.links[anchor].target == p
                    pred.links[anchor].target = inner.merged
        else:
            self.logger.info("impossible to aggregate %r", page)
            inner.aggregation = PageMapper.AGGREG_IMPOSS
            for p,v in inner.iteritems():
                assert p == v, "%r != %r" % (p, v)
                if p.aggregation != PageMapper.AGGREG_PENDING:
                    # XXX AGGREG_PENDING is used to explude nodes from plot
                    p.aggregation = PageMapper.AGGREG_IMPOSS




    def aggregatable(self, page):
        # aggregate only if all the links across aggregatable pages
        # point to the same page
        inner = self.first[page.templetized]
        for i,l in page.links.enumerate():
            targetset = set(p.links[i].target for p in inner
                if p.aggregation != PageMapper.AGGREG_PENDING)
            #print "TARGETSET", targetset
            if len(targetset) > 1 and \
                    not all(p in inner for p in targetset):
                # different pages have different outgoing links
                # and they do not point to pages in the aggregatable set
                return False
        return True

    def setLatest(self, page):
        inner = self.first[page.templetized]
        assert inner.aggregation in [PageMapper.NOT_AGGREG,
                PageMapper.STATUS_SPLIT], "Mixing aggregation and status splitting not supported yet"
        inner.latest = page
        if inner.aggregation == PageMapper.STATUS_SPLIT:
            inner[page.exact] = page
            page.aggregation = PageMapper.STATUS_SPLIT
        else:
            assert len(inner) == 1, "page %r inner %r" % (page, inner.pages)
            oldpage = inner.itervalues().next()
            assert oldpage.exact != page.exact
            inner.clear()
            inner[oldpage.exact] = oldpage
            inner[page.exact] = page
            assert len(inner) == 2, len(inner)
            inner.aggregation = PageMapper.STATUS_SPLIT
            oldpage.aggregation = PageMapper.STATUS_SPLIT
            page.aggregation = PageMapper.STATUS_SPLIT


    def findClone(self, page, link, target):
        assert page.links[link].target != target
        inner = self.first[page.templetized]
        for p in inner.itervalues():
            ptarget = p.links[link].target
            if (ptarget.aggregation != PageMapper.STATUS_SPLIT and \
                    ptarget == target) or \
                    ptarget.exact == target.exact:
                assert p.exact != page.exact, "%r->%r, %r->%r, %r" \
                        % (p, p.target, page, page.links[link].target,
                                target)
                return p
        return None

class Page:
    HASHVALFMT = 'i'
    HASHVALFNMTSIZE = struct.calcsize(HASHVALFMT)

    def __init__(self, url, anchors=[], forms=[], cookies=[]):
        self.url = url
        self.links = Links(anchors, forms)
        self.cookies = cookies
        self.str = 'Page(%s)' % ','.join([str(self.url),
            self.links.hashData(),
            str(self.cookies)])
        self.histories = []
        self.calchash()
        self.backlinks = set()
        self.aggregation = PageMapper.NOT_AGGREG
        self.templetized = TempletizedPage(self)
        self.exact = ExactPage(self)

    def calchash(self):
        self.hashval = self.str.__hash__()

    def __hash__(self):
        return self.hashval

    def __eq__(self, rhs):
        return self.hashval == rhs.hashval

    def __repr__(self):
        return self.str

    def linkto(self, idx, targetpage):
        link = self.links[idx]
        assert not link.nvisits
        link.nvisits += 1
        link.target = targetpage
        link.history = self.histories[-1]
        targetpage.backlinks.add((self, idx))

    def getUnvisitedLink(self):
        return self.links.getUnvisited()

    def clone(self):
        cloned = copy.copy(self)
        cloned.histories = []
        cloned.backlinks = set()
        cloned.links = self.links.clone()
        cloned.exact = ExactPage(cloned)
        #assert False, "XXX define str"
        return cloned



class TempletizedPage(Page):

    def __init__(self, page):
        self.page = page
        self.str = 'TempletizedPage(%s)' % \
                ','.join([page.links.strippedHashData(),
                    str(page.cookies)])
        self.calchash()

class ExactPage(Page):

    def __init__(self, page):
        self.page = page
        self.str = "Exact" + page.str
        self.hashval = id(self.page)


class FormFiller:
    def __init__(self):
        self.forms = {}

    def add(self, k):
        self.forms[tuple(sorted(k.keys()))] = k

    def __getitem__(self, k):
        return self.forms[tuple(sorted([i for i in k if i]))]


import htmlunit

htmlunit.initVM(':'.join([htmlunit.CLASSPATH, '.']))

class CrawlerEmptyHistory(Exception):
    pass

class CrawlerActionFailure(Exception):
    pass

class CrawlerUnsubmittableForm(CrawlerActionFailure):
    pass

class Crawler:

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.webclient = htmlunit.WebClient()

    def open(self, url):
        self.history = []
        htmlpage = htmlunit.HtmlPage.cast_(self.webclient.getPage(url))
        return self.newPage(htmlpage)

    def createAnchor(self, a):
        return Anchor(a.getHrefAttribute())

    def createForm(self, f):
        inputs = [n.getAttribute('name') for n in
            htmlunit.HtmlElementWrapper(f).getHtmlElementsByTagName('input')]
        return Form(method=f.getMethodAttribute(),
                action=f.getActionAttribute(),
                inputs=inputs)

    def updateInternalData(self, htmlpage):
        self.htmlpage = htmlpage
        htmlpagewrapped = htmlunit.HtmlPageWrapper(htmlpage)
        self.url = htmlpage.getWebResponse().getRequestUrl().toString()
        self.anchors = [a for a in htmlpagewrapped.getAnchors()
                if not a.getHrefAttribute().split(':', 1)[0].lower()
                in ['mailto']]
        self.forms = [f for f in htmlpagewrapped.getForms()]
#                if f.getMethodAttribute().lower() == 'get']
        self.page = Page(url=self.url,
                anchors=[self.createAnchor(a) for a in self.anchors],
                forms=[self.createForm(f) for f in self.forms])

    def newPage(self, htmlpage):
        self.updateInternalData(htmlpage)
        return self.page

    def clickAnchor(self, idx):
        self.history.append(self.htmlpage)
        try:
            htmlpage = self.anchors[idx].click()
        except htmlunit.JavaError, e:
            javaex = e.getJavaException()
            if not htmlunit.FailingHttpStatusCodeException.instance_(javaex):
                raise
            javaex = htmlunit.FailingHttpStatusCodeException.cast_(javaex)
            ecode = javaex.getStatusCode()
            emsg = javaex.getStatusMessage()
            self.logger.warn("%d %s, %s", ecode, emsg,
                    self.anchors[idx].getHrefAttribute())
            return self.errorPage(ecode)
        return self.newPage(htmlpage)

    def printChild(self, e, n=0):
        print ' '*n, e
        try:
            for i in e.getChildElements():
                self.printChild(i, n+1)
        except AttributeError:
            pass

    def submitForm(self, idx, params):
        htmlpage = None

        form = self.forms[idx]

        self.logger.info("submitting form %s %r and params: %r",
                form.getMethodAttribute().upper(), form.getActionAttribute(),
                params)

#        print "===========", self.url
#        for f in self.forms:
#            print "***", f
#            for n in htmlunit.HtmlElementWrapper(f).getHtmlElementsByTagName("input"):
#                print n
#            self.printChild(f)

        for k,v in params.iteritems():
            form.getInputByName(k).setValueAttribute(v)

        try:
            # find an element to click in order to submit the form
            # TODO: explore clickable regions in input type=image
            for submittable in [("input", "type", "submit"),
                    ("input", "type", "image"),
                    ("button", "type", "submit")]:
                try:
                    submitter = self.forms[idx].\
                            getOneHtmlElementByAttribute(*submittable)
                    htmlpage = submitter.click()
                    break
                except htmlunit.JavaError, e:
                    javaex = e.getJavaException()
                    if not htmlunit.ElementNotFoundException.instance_(javaex):
                        raise
                    continue

            if not htmlpage:
                self.logger.warn("could not find submit button for form %s %r in page %r",
                        form.getMethodAttribute().upper(),
                        form.getActionAttribute(), self.url)
                raise CrawlerUnsubmittableForm()
        except htmlunit.JavaError, e:
            javaex = e.getJavaException()
            if not htmlunit.FailingHttpStatusCodeException.instance_(javaex):
                raise
            javaex = htmlunit.FailingHttpStatusCodeException.cast_(javaex)
            ecode = javaex.getStatusCode()
            emsg = javaex.getStatusMessage()
            self.logger.warn("%d %s, %s", ecode, emsg,
                    self.anchors[idx].getHrefAttribute())
            self.history.append(self.htmlpage)
            return self.errorPage(ecode)

        self.history.append(self.htmlpage)
        return self.newPage(htmlpage)


    def back(self):
        # htmlunit has not "back" functrion
        try:
            htmlpage = self.history.pop()
        except IndexError:
            raise CrawlerEmptyHistory()
        self.updateInternalData(htmlpage)
        return self.page

    def errorPage(self, code):
        self.page = Page(url="%d" % code)
        return self.page


class Engine:
    def __init__(self, formfiller=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.formfiller = formfiller

    def findPathToUnvisited_(self, page, what, how):
        seen = set([page])
        heads = {page: []}
        newheads = {}
        while heads:
            for h,p in heads.iteritems():
                unvisited = h.links.getUnvisited(what)
                if unvisited:
                    assert (h, unvisited) in self.pagemap.unvisited, "page %r  link %r not in %r" % (h, unvisited, self.pagemap.unvisited)
                    # exclude the starting page from the path
                    return [i for i in reversed([h]+p[:-1])]
                newpath = [h]+p
                newheads.update((newh, newpath) for newh in
                    (set(l.target for l in self.pagemap[h].links.iter(how)
                        if l.target and not l.ignore) - seen))
                seen |= set(newheads.keys())
            heads = newheads
            newheads = {}


    def findPathToUnvisited(self, page):
        if self.pagemap.unvisited.len(Links.ANCHOR):
            path = self.findPathToUnvisited_(page, Links.ANCHOR, Links.ANCHOR)
            if not path:
                self.logger.info("unexplored anchors not reachable using anchors")
                path = self.findPathToUnvisited_(page, Links.ANCHOR, Links.FORM)
            if path:
                return path
                self.logger.warn("unexplored anchors not reachable!")
        if self.pagemap.unvisited.len(Links.FORM):
            path = self.findPathToUnvisited_(page, Links.FORM, Links.ANCHOR)
            if not path:
                self.logger.info("unexplored anchors not reachable using forms")
                path = self.findPathToUnvisited_(page, Links.FORM, Links.FORM)
            if path:
                return path
            self.logger.warn("unexplored forms not reachable!")

    def navigatePath(self, page, path):
        assert page == self.cr.page
        for p in path:
            linkidx = None
            for i,l in page.links.enumerate():
                if l.target == p:
                    linkidx = i
                    break
            assert linkidx != None
            newpage = self.doAction(page, linkidx)
            link = page.links[linkidx]
            if newpage != p:
                if link.nvisits == 1:
                    self.logger.info('overriding link target "%s" with "%s"',
                            p, newpage)
                    link.nvisits = 0
                    self.updateOutLinks(page, linkidx, newpage)
                else:
                    self.logger.info('unexpected link target "%s" instead of "%s"',
                            newpage, p)
                    clonedpage = self.splitPage(page, linkidx, newpage)
                    self.history = clonedpage.histories[-1] + \
                            [(clonedpage, linkidx)]
                    newpage.histories[-1] = self.history[:]
                return newpage
            else:
                link.nvisits += 1
            page = newpage
        return page

    def splitPage(self, page, linkidx, newpage):
        self.logger.info("splitting %r", page)
        clonedpage = self.pagemap.findClone(page, linkidx, newpage)
        if not clonedpage:
            self.logger.info("splitting %r", page)
            clonedpage = page.clone()
            assert clonedpage.links.__iter__().next().nvisits == 0
        assert clonedpage.exact != page.exact, "%x %x" % \
                (id(clonedpage.exact), id(page.exact))
        # proppagate the change of status backwards
        prevpage, prevlinkidx = page.histories[-1][-1]
        prevlink =  prevpage.links[prevlinkidx]
        assert prevlink.nvisits > 0
        if prevlink.nvisits > 1:
            clonedprev = self.splitPage(prevpage, prevlinkidx, clonedpage)
            # XXX remove last history form the original page being cloned
            clonedpage.histories.append(clonedprev.histories[-1] +
                    [(clonedprev, prevlinkidx)])
        else:
            prevlink.target = clonedpage
            clonedpage.histories.append(page.histories[-1])
        # linkto() must be called affter setting history
        if clonedpage.links[linkidx].nvisits:
            # pre-existing clone
            assert clonedpage.links[linkidx].target == newpage
            clonedpage.links[linkidx].nvisits += 1
        else:
            clonedpage.linkto(linkidx, newpage)
        self.pagemap.setLatest(clonedpage)
        return clonedpage


    def processPage(self, page):
        if page.aggregation == PageMapper.AGGREG_PENDING:
            # XXX in this way we forse the crawler to use the "back" function
            # instead of using a potential back-link; what happen if the latest 
            # page we are not exploring changes the in-server status?
            self.logger.info("not exploring additional aggregatable pages")
            prevpage, prevlink = page.histories[-1][-1]
            prevpage.links[prevlink].ignore = True
            return None
        return page.getUnvisitedLink()

    def findNextStep(self, page):
        nextAction = None
        while nextAction == None:
            nextAction = self.processPage(page)
            if nextAction == None:
                path = None
                if self.pagemap.unvisited:
                    self.pagemap.unvisited.logInfo()
                    if page.aggregation != PageMapper.AGGREG_PENDING:
                        path = self.findPathToUnvisited(page)
                else:
                    self.logger.info("we are done")
                    # we are done
                    return (None, page)
                if path:
                    self.logger.debug("found path: %r", path)
                    page = self.navigatePath(page, path)
                    nextAction = self.processPage(page)
                else:
                    self.logger.info("no path found, stepping back")
                    page = self.cr.back()
                    page = self.pagemap[page]
        self.logger.debug("next action %r", nextAction)
        return (nextAction, page)

    def doAction(self, page, action):
        if action[0] == Links.ANCHOR:
            newpage = self.cr.clickAnchor(action[1])
        elif action[0] == Links.FORM:
            try:
                formkeys = page.links[action].getFormKeys()
                params = self.formfiller[formkeys]
            except KeyError:
                # we do not have parameters for the form
                params = {}
            newpage = self.cr.submitForm(action[1], params)
        else:
            assert False, "Unknown action %r" % action

        # use reference to the pre-existing page
        newpage = self.pagemap[newpage]

        self.history.append((page, action))
        newpage.histories.append(self.history[:])

        return newpage


    def updateOutLinks(self, page, action, newpage):
        page.linkto(action, newpage)
        try:
            self.pagemap.unvisited.remove(page, action)
        except KeyError:
            # might have been alredy removed by a page merge
            pass

    def main(self, url):
        self.cr = Crawler()
        self.pagemap = PageMapper()
        self.history = []
        page = self.cr.open(url)
        page = self.pagemap[page]
        assert len(page.histories) == 0
        page.histories = [[]]
        nextAction = self.processPage(page)
        while nextAction != None:
            try:
                newpage = self.doAction(page, nextAction)
                self.updateOutLinks(page, nextAction, newpage)

                self.pagemap.checkAggregatable(page)
                page = newpage
            except CrawlerActionFailure:
                page.links[nextAction].ignore = True
                self.pagemap.unvisited.remove(page, nextAction)

            nextAction, page = self.findNextStep(page)

    def writeDot(self):
        self.logger.info("creating DOT graph")
        dot = pydot.Dot()
        nodes = {}
        for p in self.pagemap:
            if p.aggregation != PageMapper.AGGREG_PENDING:
                url = urlparse.urlparse(p.url)
                name = url.path
                if url.query:
                    name += '?' + url.query
                name += '@%x' % id(p)
                node = pydot.Node(name)
                if p.aggregation == PageMapper.AGGREGATED:
                    node.set_color('green')
                elif p.aggregation == PageMapper.AGGREG_IMPOSS:
                    node.set_color('red')
                nodes[p.exact] = node

        self.logger.debug("%d DOT nodes", len(nodes))

        for n in nodes.itervalues():
            dot.add_node(n)

        for n,dn in nodes.iteritems():
            n = n.page
            src = dn
            links = defaultdict(int)
            for dst in n.links:
                if not dst.target:
                    self.logger.warn("found null target")
                elif dst.target.aggregation != PageMapper.AGGREG_PENDING:
                    if dst.__class__.__name__ == 'Form':
                        if dst.method == 'post':
                            color = 'purple'
                        else:
                            color = 'blue'
                    else:
                        color = 'black'
                    if not dst.nvisits:
                        style = 'dotted'
                    else:
                        style = 'solid'
                    links[(dst.target, color, style)] += 1
            for k,num in links.iteritems():
                try:
                    target, color, style = k
                    edge = pydot.Edge(src, nodes[target.exact])
                    edge.set_color(color)
                    edge.set_style(style)
                    edge.set_label("%d" % num)
                    dot.add_edge(edge)
                except KeyError:
                    self.logger.error("unable to find target node")

        dot.write_ps('graph.ps')
        #dot.write_pdf('graph.pdf')
        with open('graph.dot', 'w') as f:
            f.write(dot.to_string())
        self.logger.debug("DOT graph written")

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    ff = FormFiller()
    login = {'username': 'ludo', 'password': 'duuwhe782osjs'}
    ff.add(login)
    login = {'user': 'ludo', 'pass': 'ludo'}
    ff.add(login)
    e = Engine(ff)
    try:
        e.main(sys.argv[1])
    except:
        raise
    finally:
        try:
            e.writeDot()
        except:
            import traceback
            traceback.print_exc()








