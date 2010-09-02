#!/usr/bin/env python

import logging
import urlparse
import re

import output

import htmlunit

from collections import defaultdict, deque

htmlunit.initVM(':'.join([htmlunit.CLASSPATH, '.']))

# running htmlunit via JCC will override the signal halders

import signal

def signalhandler(signum, frame):
    raise KeyboardInterrupt

#signal.signal(signal.SIGUSR1, signalhandler)
signal.signal(signal.SIGINT, signalhandler)

def median(l):
    s = sorted(l)
    ln = len(s)
    if ln % 2 == 0:
        return float(s[ln/2]+s[ln/2-1])/2
    else:
        return float(s[ln/2])

class lazyproperty(object):
    """ from http://blog.pythonisito.com/2008/08/lazy-descriptors.html """

    def __init__(self, func):
        self._func = func
        self.__name__ = func.__name__
        self.__doc__ = func.__doc__

    def __get__(self, obj, klass=None):
        if obj is None: return None
        result = obj.__dict__[self.__name__] = self._func(obj)
        return result

class RecursiveDict(defaultdict): 
    def __init__(self, nleavesfunc=lambda x: 1): 
        self.default_factory = RecursiveDict
        # when counting leaves, apply this function to non RecursiveDict objects
        self.nleavesfunc = nleavesfunc

    @lazyproperty
    def nleaves(self):
        """ number of objects in the tis subtree of the nested dictionay
        NOTE: this is a lazy property, will get stale if tree is updated!
        """
        return sum(i.nleaves if isinstance(i, self.default_factory) else self.nleavesfunc(i)
                for i in self.itervalues())

    def getpath(self, path):
        i = self
        for p in path:
            i = i[p]
        return i

    def setpath(self, path, value):
        i = self
        for p in path[:-1]:
            i = i[p]
        i[path[-1]] = value

    def applypath(self, path, func):
        i = self
        for p in path[:-1]:
            i = i[p]
        i[path[-1]] = func(i[path[-1]])

    def setapplypath(self, path, value, func):
        i = self
        for p in path[:-1]:
            i = i[p]
        if path[-1] in i:
            i[path[-1]] = func(i[path[-1]])
        else:
            i[path[-1]] = value

    def iterlevels(self):
        if self:
            queue = deque([(self,)])
            while queue:
                l = queue.pop()
                levelkeys = []
                children = []
                for c in l:
                    if isinstance(c, self.default_factory):
                        levelkeys.extend(c.iterkeys())
                        children.extend(c.itervalues())
                        queue.append(children)
                    else:
                        levelkeys.append(c)
                yield levelkeys




class Request(object):

    def __init__(self, webrequest):
        self.webrequest = webrequest

    @lazyproperty
    def method(self):
        return self.webrequest.getHttpMethod()

    @lazyproperty
    def path(self):
        return self.webrequest.getUrl().getPath()

    @lazyproperty
    def query(self):
        return self.webrequest.getUrl().getQuery()

    @lazyproperty
    def fullpath(self):
        fullpath = self.path
        if self.query:
            fullpath += "?" + self.query
        return fullpath

    @lazyproperty
    def params(self):
        raise NotImplemented

    @lazyproperty
    def cookies(self):
        raise NotImplemented

    @lazyproperty
    def headers(self):
        raise NotImplemented

    @lazyproperty
    def _str(self):
        return "Request(%s %s)" % (self.method, self.fullpath)

    @lazyproperty
    def urlvector(self):
        return urlvector(self)

    def __str__(self):
        return self._str

    def __repr__(self):
        return str(self)


class Response(object):

    def __init__(self, webresponse, page):
        self.webresponse = webresponse
        self.page = page

    @lazyproperty
    def code(self):
        return self.webresponse.getStatusCode()

    @lazyproperty
    def message(self):
        return self.webresponse.getStatusMessage()

    @lazyproperty
    def content(self):
        raise NotImplemented

    @lazyproperty
    def cookies(self):
        raise NotImplemented

    def __str__(self):
        return "Response(%d %s)" % (self.code, self.message)


class RequestResponse(object):

    def __init__(self, page, prev=None, next=None, backto=None):
        webresponse = page.getWebResponse()
        self.response = Response(webresponse, Page(page, self))
        self.request = Request(webresponse.getWebRequest())
        self.prev = prev
        self.next = next
        # how many pages we went back before performing this new request
        self.backto = backto

    def __iter__(self):
        curr = self
        while curr:
            yield curr
            curr = curr.next

    def __str__(self):
        return "%s -> %s" % (self.request, self.response)


class Link(object):

    xpathsimplifier = re.compile(r"\[[^\]*]\]")

    def __init__(self, internal, reqresp):
        self.internal = internal
        self.reqresp = reqresp
        self.to = []

    @lazyproperty
    def dompath(self):
        return Link.xpathsimplifier.sub("", self.internal.getCanonicalXPath())
        return self.internal.getCanonicalXPath()

    @lazyproperty
    def _str(self):
        raise NotImplementedError

    def __str__(self):
        return self._str

    def __repr__(self):
        return str(self)



class Anchor(Link):

    @lazyproperty
    def href(self):
        return self.internal.getHrefAttribute()

    @lazyproperty
    def hrefurl(self):
        return urlparse.urlparse(self.href)

    def click(self):
        return self.internal.click()

    @lazyproperty
    def _str(self):
        return "Anchor(%s, %s)" % (self.href, self.dompath)

    @lazyproperty
    def linkvector(self):
        return urlvector(self.hrefurl)


class Form(Link):

    @lazyproperty
    def _str(self):
        return "Form"


class Page(object):

    def __init__(self, internal, reqresp):
        self.internal = internal
        self.reqresp = reqresp
        self.abspage = None

    @lazyproperty
    def anchors(self):
        return [Anchor(i, self.reqresp) for i in self.internal.getAnchors()]

    @lazyproperty
    def forms(self):
        return [Form(i, self.reqresp) for i in self.internal.getForms()]

    @lazyproperty
    def linkstree(self):
        return linkstree(self)

    @lazyproperty
    def linksvector(self):
        return linksvector(self)

class AbstractLink(object):

    def __init__(self, abspage):
        self.abspage = abspage
        # map from state to AbstractRequest
        self.targets = {}

    @lazyproperty
    def _str(self):
        raise NotImplementedError

    def __str__(self):
        return self._str

    def __repr__(self):
        return str(self)

class AbstractAnchor(AbstractLink):
    
    @lazyproperty
    def _str(self):
        return "AbstractAnchor(targets=%s)" % self.targets


class AbstractForm(AbstractLink):
    pass

class AbstractPage(object):

    def __init__(self, reqresps):
        self.reqresps = reqresps
        # TODO: number of links might not be the same in some more complex clustering
        self.absanchors = [AbstractAnchor(i) for i in zip(*(rr.response.page.anchors for rr in reqresps))]
        self.absforms = [AbstractForm(i) for i in zip(*(rr.response.page.forms for rr in reqresps))]
        self.statelinkmap = {}

    @lazyproperty
    def _str(self):
        return "AbstractPage(%s)" % set(str(i.request.fullpath) for i in self.reqresps)

    def __str__(self):
        return self._str

    def __repr__(self):
        return str(self)


class AbstractRequest(object):

    def __init__(self, request):
        # map from state to AbstractPage
        self.request = request
        self.targets = {}

    def __str__(self):
        return "AbstractRequest(%s)" % self.request

    def __repr__(self):
        return str(self)

class Target(object):
    def __init__(self, target, transition, nvisits=0):
        self.target = target
        self.transition = transition
        self.nvisits = nvisits

    def __str__(self):
        return "Target(%r, transition=%d, nvisits=%d)" % \
                (self.target, self.transition, self.nvisits)

    def __repr__(self):
        return str(self)


class Buckets(dict):

    def __init__(self, h=hash):
        self.h = h

    def __missing__(self, k):
        v = []
        self[k] = v
        return v

    def add(self, obj, h=None):
        if h is None:
            h = self.h
        v = self[h(obj)]
        v.append(obj)
        return v


class AbstractMap(dict):

    def __init__(self, absobj, h=hash):
        self.h = h
        self.absobj = absobj

    def __missing__(self, k):
        v = self.absobj(k)
        self[k] = v
        return v

    def getAbstract(self, obj):
        return self[self.h(obj)]

    def __iter__(self):
        return self.itervalues()

def urlvector(request):
    """ /path/to/path.html?p1=v1&p2=v2
        ->
        ['path', 'to', 'page.html', ('p1', 'p2'), ('v1', 'v2')]
    """
    urltoks = request.path.split('/')
    query = request.query
    if query:
        querytoks = request.query.split('&')
        keys, values = zip(*(i.split('=') for i in querytoks))
        urltoks.append(tuple(keys))
        urltoks.append(tuple(values))
    return tuple(urltoks)

def linkstree(page):
    # leaves in linkstree are counter of how many times that url occurred
    # therefore use that counter when compuing number of urls with "nleaves"
    linkstree = RecursiveDict(lambda x: x)
    if page.anchors:
        for a in page.anchors:
            urlv = [a.dompath] + list(a.linkvector)
            # set leaf to 1 or increment
            linkstree.setapplypath(urlv, 1, lambda x: x+1)
    else:
        # all pages with no links will end up in the same special bin
        linkstree.setapplypath(("<EMPTY>", ), 1, lambda x: x+1)
    return linkstree


def likstreedist(a, b):
    raise NotImplementedError

def linksvector(page):
    linksvector = tuple([tuple(i) for i in page.linkstree.iterlevels()])
    return linksvector


class Classfier(RecursiveDict):

    def __init__(self, featuresextractor):
        self.featuresextractor = featuresextractor
        # leaves should return the number of elements in the list for nleaves
        RecursiveDict.__init__(self, lambda x: len(x))

    def add(self, obj):
        featvect = self.featuresextractor(obj)
        # hack to use lambda function instead of def func(x); x.append(obj); return x
        self.setapplypath(featvect, [obj], lambda x: (x.append(obj), x)[1])

    def addall(self, it):
        for i in it:
            self.add(i)


class PageClusterer(object):

    def simplehash(self, reqresp):
        page = reqresp.response.page
        hashedval = reqresp.request.path + ', ' + repr(page.anchors) + ", " + repr(page.forms)
        return hashedval

    def __init__(self, reqresps):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.debug("clustering pages")

        self.levelclustering(reqresps)
        self.simpleclustering(reqresps)

    def simpleclustering(self, reqresps):
        buckets = Buckets(self.simplehash)
        cnt = 0
        for i in reqresps:
            buckets.add(i)
            cnt += 1
        self.logger.debug("clustered %d pages into %d clusters", cnt, len(buckets))
        abspages = [AbstractPage(i) for i in buckets.itervalues()]
        for ap in abspages:
            for rr in ap.reqresps:
                rr.response.page.abspage = ap
        self.logger.debug("%d abstract pages generated", len(abspages))
        self.abspages = abspages

    def printlevestat(self, level, n=0):
        med = median((i.nleaves if hasattr(i, "nleaves") else len(i) for i in level.itervalues()))
        self.logger.debug(output.green(' ' * n + "MED %f / %d"), med, level.nleaves )
        for k, v in level.iteritems():
            nleaves = v.nleaves if hasattr(v, "nleaves") else len(v)
            self.logger.debug(output.green(' ' * n + "K %s %d %f"), k, nleaves, nleaves/med)
            if hasattr(v, "iteritems"):
                self.printlevestat(v, n+1)

    def levelclustering(self, reqresps):
        classif = Classfier(lambda rr: rr.response.page.linksvector)
        classif.addall(reqresps)
        self.printlevestat(classif)



    def getAbstractPages(self):
        return self.abspages


class AppGraphGenerator(object):

    def __init__(self, reqrespshead, abspages):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.reqrespshead = reqrespshead
        self.abspages = abspages

    def generateAppGraph(self):
        self.logger.debug("generating application graph")

        # make sure we are at the beginning
        assert self.reqrespshead.prev is None
        curr = self.reqrespshead
        laststate = 0

        # map requests with same "signature" to the same AbstractRequest object
        reqmap = AbstractMap(AbstractRequest, lambda x: repr(x))
        self.reqmap = reqmap
        currabsreq = reqmap.getAbstract(curr.request)
        self.headabsreq = currabsreq

        # go through the while navigation path and link together AbstractRequests and AbstractPages
        # for now, every request will generate a new state, post processing will happen late
        cnt = 0
        while curr:
            currpage = curr.response.page
            currabspage = currpage.abspage
            assert not laststate in currabsreq.targets
            currabsreq.targets[laststate] = Target(currabspage, laststate+1)
            laststate += 1

            if curr.next:
                if curr.next.backto is not None:
                    currpage = curr.next.backto.response.page
                    currabspage = currpage.abspage
                # find which link goes to the next request in the history
                # TODO do not limit to anchors
                chosenlink = (i for i, l in enumerate(currpage.anchors) if curr.next in l.to).next()
                nextabsreq = reqmap.getAbstract(curr.next.request)
                # XXX we cannot just use the index for more complex clustering
                assert not laststate in currabspage.absanchors[chosenlink].targets
                currabspage.absanchors[chosenlink].targets[laststate] = Target(nextabsreq, laststate)
                assert not laststate in currabspage.statelinkmap
                currabspage.statelinkmap[laststate] = currabspage.absanchors[chosenlink]


            curr = curr.next
            currabsreq = nextabsreq
            cnt += 1

        self.maxstate = laststate
        self.logger.debug("application graph generated in %d steps", cnt)

    def getMinMappedState(self, state, statemap):
        prev = state
        mapped = statemap[state]
        while mapped != prev:
            prev = mapped
            mapped = statemap[mapped]
        return mapped


    def reduceStates(self):
        self.logger.debug("reducing state number from %d", self.maxstate)

        # map each state to its equivalent one
        statemap = range(self.maxstate+1)

        currreq = self.headabsreq

        # need history to handle navigatin back; pair of (absrequest,absresponse)
        history = []
        currstate = 0
        
        while True:
            respage = currreq.targets[currstate].target
            history.append((currreq, respage))
            currstate += 1
            statemap[currstate] = currstate-1
            if currstate not in respage.statelinkmap:
                if currstate == self.maxstate:
                    # end reached
                    break
                while currstate not in respage.statelinkmap:
                    history.pop()
                    respage = history[-1][1]

            chosenlink = respage.statelinkmap[currstate]
            chosentarget = chosenlink.targets[currstate].target
            assert currstate in chosenlink.targets
            # find if there are other states that we have already processed that lead to a different target
            smallerstates = sorted([i for i, t in chosenlink.targets.iteritems() if i < currstate and t != chosentarget], reverse=True)
            if smallerstates:
                currmapsto = self.getMinMappedState(currstate, statemap)
                for ss in smallerstates:
                    ssmapsto = self.getMinMappedState(ss, statemap)
                    if ssmapsto != currmapsto:
                        # TODO need to split current state!
                        raise NotImplementedError
            
            currreq = chosentarget

        for i in range(len(statemap)):
            statemap[i] = self.getMinMappedState(i, statemap)

        nstates = len(set(statemap))

        self.logger.debug("final states %d, collapsing graph", nstates)

        # merge states that were reduced to the same one
        # and update visit counter
        for ap in self.abspages:
            for aa in ap.absanchors:
                statereduce = [(st, statemap[st]) for st in aa.targets]
                for st, goodst in statereduce:
                    if goodst in aa.targets:
                        assert aa.targets[st].target == aa.targets[goodst].target, \
                            "%s %s" % (aa.targets[st], aa.targets[goodst])
                    else:
                        aa.targets[goodst] = aa.targets[st]
                        del aa.targets[st]
                        assert aa.targets[goodst].nvisits == 0
                    aa.targets[goodst].nvisits += 1

        for ar in self.reqmap:
            statereduce = [(st, statemap[st]) for st in ar.targets]
            for st, goodst in statereduce:
                if goodst in ar.targets:
                    assert ar.targets[st].target == ar.targets[goodst].target
                else:
                    ar.targets[goodst] = ar.targets[st]
                    del ar.targets[st]
                    assert ar.targets[goodst].nvisits == 0
                ar.targets[goodst].nvisits += 1

        # return last current state
        return statemap[-1]



class DeferringRefreshHandler(htmlunit.RefreshHandler):

    def __init__(self, refresh_urls=[]):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.refresh_urls = refresh_urls

    def handleRefresh(self, page, url, seconds):
        self.logger.debug("%s refrsh to %s in %d s", page, url, seconds)
        self.refresh_urls.append(url)


class Crawler(object):

    class EmptyHistory(Exception):
        pass

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        #self.webclient = htmlunit.WebClient(htmlunit.BrowserVersion.INTERNET_EXPLORER_6)
        #self.webclient = htmlunit.WebClient(htmlunit.BrowserVersion.FIREFOX_3)
        #bw = htmlunit.BrowserVersion(
        #    htmlunit.BrowserVersion.NETSCAPE, "5.0 (Windows; en-US)",
        #            "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.1) Gecko/2008070208 Firefox/3.0.1 Safari", 3.0)
            #        "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.9.0.1) Gecko/2008070208 Firefox/3.0.1 Safari", "1.2", 3.0, "FF3", None)

        #self.webclient = htmlunit.WebClient(bw)
        self.webclient = htmlunit.WebClient()
        self.webclient.setThrowExceptionOnScriptError(True);
        self.webclient.setUseInsecureSSL(True)
        self.webclient.setRedirectEnabled(False)
        self.refresh_urls = []
        # XXX the refresh handler does not tget called, why?
        self.webclient.setRefreshHandler(DeferringRefreshHandler(self.refresh_urls))
        # last generated RequestResponse object
        self.lastreqresp = None
        # current RequestResponse object (will differ from lastreqresp when back() is invoked)
        self.currreqresp = None
        # first RequestResponse object
        self.headreqresp = None

    def open(self, url):
        del self.refresh_urls[:]
        #webrequest = htmlunit.WebRequest(htmlunit.URL(url))
        htmlpage = htmlunit.HtmlPage.cast_(self.webclient.getPage(url))
        # TODO: handle HTTP redirects, they will throw an exception
        return self.newPage(htmlpage)

    def newPage(self, htmlpage):
        self.updateInternalData(htmlpage)
        self.logger.info("%s", self.currreqresp)
        return self.currreqresp

    def updateInternalData(self, htmlpage):
        backto = self.currreqresp if self.lastreqresp != self.currreqresp else None
        newreqresp = RequestResponse(htmlpage, self.lastreqresp, backto=backto)
        if self.lastreqresp is not None:
            self.lastreqresp.next = newreqresp
        self.lastreqresp = newreqresp
        self.currreqresp = newreqresp
        if self.headreqresp is None:
            self.headreqresp = newreqresp

    def click(self, anchor):
        self.logger.debug("clicking on %s", anchor)
        assert anchor.internal.getPage() == self.currreqresp.response.page.internal, \
                "Inconsistency error %s != %s" % (anchor.internal.getPage(), self.currreqresp.response.page.internal)
        htmlpage = htmlunit.HtmlPage.cast_(anchor.internal.click())
        # TODO: handle HTTP redirects, they will throw an exception
        reqresp = self.newPage(htmlpage)
        anchor.to.append(reqresp)
        assert reqresp.request.fullpath[-len(anchor.href):] == anchor.href, \
                "Unhandled redirect %s !sub %s" % (anchor.href, reqresp.request.fullpath)
        return reqresp

    def back(self):
        self.logger.debug("stepping back")
        # htmlunit has not "back" functrion
        if self.currreqresp.prev is None:
            raise Crawler.EmptyHistory()
        self.currreqresp = self.currreqresp.prev
        return self.currreqresp


class Engine(object):

    BACK, ANCHOR = range(2)

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.state = -1

    def getUnvisitedLink(self, reqresp):
        page = reqresp.response.page
        abspage = page.abspage

        # if we do not have an abstract graph, pick first anchor
        # this should happen only at the first iteration
        if abspage is None:
            if len(page.anchors) > 0:
                self.logger.debug("abstract page not availabe, picking first anchor")
                return page.anchors[0]
            else:
                self.logger.debug("abstract page not availabe, and no anchors")
                return None

        # find unvisited link
        for i, aa in enumerate(abspage.absanchors):
            if self.state not in aa.targets or aa.targets[self.state].nvisits == 0:
                return reqresp.response.page.anchors[i]
        return None

    def getNextAction(self, reqresp):
        unvisited = self.getUnvisitedLink(reqresp)
        if unvisited is not None:
            return (Engine.ANCHOR, unvisited)
        return (Engine.BACK, )

    def main(self, urls):
        cr = Crawler()

        for cnt, url in enumerate(urls):
            self.logger.info("starting with URL %d/%d %s", cnt+1, len(urls), url)
            reqresp = cr.open(url)
            print output.red("TREE %s" % (reqresp.response.page.linkstree,))
            print output.red("TREEVECTOR %s" % (reqresp.response.page.linksvector,))
            nextAction = self.getNextAction(reqresp)
            while nextAction:
                if nextAction[0] == Engine.ANCHOR:
                    reqresp = cr.click(nextAction[1])
                if nextAction[0] == Engine.BACK:
                    reqresp = cr.back()
                print output.red("TREEVECTOR %s" % (reqresp.response.page.linksvector,))
                pc = PageClusterer(cr.headreqresp)
                ag = AppGraphGenerator(cr.headreqresp, pc.getAbstractPages())
                ag.generateAppGraph()
                self.state = ag.reduceStates()
                nextAction = self.getNextAction(reqresp)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    Engine().main(sys.argv[1:])


# vim:sw=4:et:
