# hgweb/hgwebdir_mod.py - Web interface for a directory of repositories.
#
# Copyright 21 May 2005 - (c) 2005 Jake Edge <jake@edge2.net>
# Copyright 2005, 2006 Matt Mackall <mpm@selenic.com>
#
# This software may be used and distributed according to the terms
# of the GNU General Public License, incorporated herein by reference.

import os
from mercurial.demandload import demandload
demandload(globals(), "ConfigParser mimetools cStringIO")
demandload(globals(), "mercurial:ui,hg,util,templater")
demandload(globals(), "mercurial.hgweb.hgweb_mod:hgweb")
demandload(globals(), "mercurial.hgweb.common:get_mtime,staticfile,style_map")
from mercurial.i18n import gettext as _

# This is a stopgap
class hgwebdir(object):
    def __init__(self, config):
        def cleannames(items):
            return [(name.strip(os.sep), path) for name, path in items]

        self.motd = ""
        self.style = ""
        self.repos_sorted = ('name', False)
        if isinstance(config, (list, tuple)):
            self.repos = cleannames(config)
            self.repos_sorted = ('', False)
        elif isinstance(config, dict):
            self.repos = cleannames(config.items())
            self.repos.sort()
        else:
            cp = ConfigParser.SafeConfigParser()
            cp.read(config)
            self.repos = []
            if cp.has_section('web'):
                if cp.has_option('web', 'motd'):
                    self.motd = cp.get('web', 'motd')
                if cp.has_option('web', 'style'):
                    self.style = cp.get('web', 'style')
            if cp.has_section('paths'):
                self.repos.extend(cleannames(cp.items('paths')))
            if cp.has_section('collections'):
                for prefix, root in cp.items('collections'):
                    for path in util.walkrepos(root):
                        repo = os.path.normpath(path)
                        name = repo
                        if name.startswith(prefix):
                            name = name[len(prefix):]
                        self.repos.append((name.lstrip(os.sep), repo))
            self.repos.sort()

    def run(self):
        if not os.environ.get('GATEWAY_INTERFACE', '').startswith("CGI/1."):
            raise RuntimeError("This function is only intended to be called while running as a CGI script.")
        import mercurial.hgweb.wsgicgi as wsgicgi
        from request import wsgiapplication
        def make_web_app():
            return self
        wsgicgi.launch(wsgiapplication(make_web_app))

    def run_wsgi(self, req):
        def header(**map):
            header_file = cStringIO.StringIO(''.join(tmpl("header", **map)))
            msg = mimetools.Message(header_file, 0)
            req.header(msg.items())
            yield header_file.read()

        def footer(**map):
            yield tmpl("footer", motd=self.motd, **map)

        url = req.env['REQUEST_URI'].split('?')[0]
        if not url.endswith('/'):
            url += '/'

        style = self.style
        if req.form.has_key('style'):
            style = req.form['style'][0]
        mapfile = style_map(templater.templatepath(), style)
        tmpl = templater.templater(mapfile, templater.common_filters,
                                   defaults={"header": header,
                                             "footer": footer,
                                             "url": url})

        def archivelist(ui, nodeid, url):
            allowed = ui.configlist("web", "allow_archive")
            for i in [('zip', '.zip'), ('gz', '.tar.gz'), ('bz2', '.tar.bz2')]:
                if i[0] in allowed or ui.configbool("web", "allow" + i[0]):
                    yield {"type" : i[0], "extension": i[1],
                           "node": nodeid, "url": url}

        def entries(sortcolumn="", descending=False, **map):
            rows = []
            parity = 0
            for name, path in self.repos:
                u = ui.ui()
                try:
                    u.readconfig(os.path.join(path, '.hg', 'hgrc'))
                except IOError:
                    pass
                get = u.config

                url = ('/'.join([req.env["REQUEST_URI"].split('?')[0], name])
                       .replace("//", "/")) + '/'

                # update time with local timezone
                try:
                    d = (get_mtime(path), util.makedate()[1])
                except OSError:
                    continue

                contact = (get("ui", "username") or # preferred
                           get("web", "contact") or # deprecated
                           get("web", "author", "")) # also
                description = get("web", "description", "")
                name = get("web", "name", name)
                row = dict(contact=contact or "unknown",
                           contact_sort=contact.upper() or "unknown",
                           name=name,
                           name_sort=name,
                           url=url,
                           description=description or "unknown",
                           description_sort=description.upper() or "unknown",
                           lastchange=d,
                           lastchange_sort=d[1]-d[0],
                           archives=archivelist(u, "tip", url))
                if (not sortcolumn
                    or (sortcolumn, descending) == self.repos_sorted):
                    # fast path for unsorted output
                    row['parity'] = parity
                    parity = 1 - parity
                    yield row
                else:
                    rows.append((row["%s_sort" % sortcolumn], row))
            if rows:
                rows.sort()
                if descending:
                    rows.reverse()
                for key, row in rows:
                    row['parity'] = parity
                    parity = 1 - parity
                    yield row

        virtual = req.env.get("PATH_INFO", "").strip('/')
        if virtual.startswith('static/'):
            static = os.path.join(templater.templatepath(), 'static')
            fname = virtual[7:]
            req.write(staticfile(static, fname, req) or
                      tmpl('error', error='%r not found' % fname))
        elif virtual:
            while virtual:
                real = dict(self.repos).get(virtual)
                if real:
                    break
                up = virtual.rfind('/')
                if up < 0:
                    break
                virtual = virtual[:up]
            if real:
                req.env['REPO_NAME'] = virtual
                try:
                    hgweb(real).run_wsgi(req)
                except IOError, inst:
                    req.write(tmpl("error", error=inst.strerror))
                except hg.RepoError, inst:
                    req.write(tmpl("error", error=str(inst)))
            else:
                req.write(tmpl("notfound", repo=virtual))
        else:
            if req.form.has_key('static'):
                static = os.path.join(templater.templatepath(), "static")
                fname = req.form['static'][0]
                req.write(staticfile(static, fname, req)
                          or tmpl("error", error="%r not found" % fname))
            else:
                sortable = ["name", "description", "contact", "lastchange"]
                sortcolumn, descending = self.repos_sorted
                if req.form.has_key('sort'):
                    sortcolumn = req.form['sort'][0]
                    descending = sortcolumn.startswith('-')
                    if descending:
                        sortcolumn = sortcolumn[1:]
                    if sortcolumn not in sortable:
                        sortcolumn = ""

                sort = [("sort_%s" % column,
                         "%s%s" % ((not descending and column == sortcolumn)
                                   and "-" or "", column))
                        for column in sortable]
                req.write(tmpl("index", entries=entries,
                               sortcolumn=sortcolumn, descending=descending,
                               **dict(sort)))
