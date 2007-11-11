#############################################
#      WikiXRay: Quantitative Analysis of Wikipedia language versions                       
#############################################
#                  http://wikixray.berlios.de                                              
#############################################
# Copyright (c) 2006-7 Universidad Rey Juan Carlos (Madrid, Spain)     
#############################################
# This program is free software. You can redistribute it and/or modify    
# it under the terms of the GNU General Public License as published by 
# the Free Software Foundation; either version 2 or later of the GPL.     
#############################################
# Author: Jose Felipe Ortega Soto                                                             

import sys,os,codecs,string, datetime, random
import dbaccess
from xml.sax import saxutils,make_parser
from xml.sax.handler import feature_namespaces, ContentHandler
from xml.sax.saxutils import XMLFilterBase, XMLGenerator
from optparse import OptionParser

class text_normalize_filter(XMLFilterBase):
    """
    SAX filter to ensure that contiguous texts nodes are merged into one
    That hopefully speeds up the parsing process a lot, specially when reading
    revisions with long text
    Receip by Uche Ogbuji, James Kew and Peter Cogolo
    Retrieved from "Python Cookbook, 2nd ed., by Alex Martelli, Anna Martelli
    Ravenscroft, and David Ascher (O'Reillly Media, 2005) 0-596-00797-3"
    """

    def __init__(self, upstream, downstream):
        XMLFilterBase.__init__(self, upstream)
        self._downstream=downstream
        self._accumulator=[]
    def _complete_text_node(self):
        if self._accumulator:
            self._downstream.characters(''.join(self._accumulator))
            self._accumulator=[]
    def characters(self, text):
        self._accumulator.append(text)
    def ignorableWhiteSpace(self, ws):
        self._accumulator.append(text)
def _wrap_complete(method_name):
    def method(self, *a, **k):
        self._complete_text_node()
        getattr(self._downstream, method_name)(*a, **k)
    method.__name__= method_name
    setattr(text_normalize_filter, method_name, method)
for n in '''startElement endElement endDocument'''.split():
    _wrap_complete(n)

class wikiHandler(ContentHandler):
    """Parse an XML file generated by Wikipedia Export page into SQL data
    suitable to be imported by MySQL"""
    def __init__(self, options):
        self.options=options
        if self.options.monitor and (not self.options.fileout and not self.options.streamout):
            self.acceso = dbaccess.get_Connection(self.options.machine, self.options.port,\
            self.options.user, self.options.passwd, self.options.database)
        self.nspace_dict={}
        self.codens=''
        self.page_dict={}
        self.rev_dict = {}
        self.stack=[]
        self.current_text = ''
        self.current_elem=None
        self.revfile=None
        self.pagefile=None
        self.page_num = 0
        self.rev_num=0
        self.last_page_len=0
        self.rev_count=0
        self.prior_rev_id='NULL'
        self.isRedirect='0'
        self.isStub='0'
        self.isMinor='0'
        self.fileErrPath="./errors.log"
        self.revinsert=''
        self.pageinsert=''
        self.revinsertrows=0
        self.revinsertsize=0
        self.pageinsertrows=0
        self.pageinsertsize=0
        self.start=datetime.datetime.now()
        self.timeCheck=None
        self.timeDelta=None
        
    def startElement(self, name, attrs):
##    Here we define which tags we want to catch
##        In this case, we only want to recall the name of the tags in a stack
##        so we can later look up the parent node of a new tag
##        (for instance, to discriminate among page id, rev id and contributor id
##        all of them with the name=="id")
        if name=='page' or name=='revision' or name=='contributor':
            self.stack.append(name)
        elif name=='namespace':
            self.codens=attrs.get('key')
        elif name=='minor':
            self.isMinor='1'
        self.current_text=''
        self.current_elem=name
        return
        
    def endElement(self, name):
##    Defining tasks to manage contents from the last readed tag
##        Catching the namespace of this page
        if name=='namespace':
            self.nspace_dict[self.current_text]=self.codens
            
        elif name=='id':
            if self.stack[-1]=='contributor':
                ##Detecting contributor's attributes inside a revision
                self.rev_dict['rev_user']=self.current_text
            elif self.stack[-1]=='revision':
                self.rev_dict[name]=self.current_text
            elif self.stack[-1]=='page':
                self.page_dict[name]=self.current_text
            else:
                self.f=open(self.fileErrPath,'w')
                if len(self.stack)>0:
                    self.f.write("Unsupported parent tag for '"+name+"': "+self.stack[-1])
                self.f.close()
                
        elif name=='ip':
            self.rev_dict['rev_user']='0'
            self.rev_dict['username']=self.current_text
            
        elif name=='timestamp':
            ##Adequate formatting of timestamps
            self.rev_dict['timestamp']=self.current_text.replace('Z','').replace('T',' ')
                
        elif name=='contributor':
            ##Pop contributor tag from the stack
            self.stack.pop()
            
        elif name=='revision':
            self.rev_count+=1
            ##Store whether this is a redirect or stub page or not
            if len(self.rev_dict['text'])>0:
                if string.upper(self.rev_dict['text'][0:9])=='#REDIRECT':
                    self.isRedirect='1'
                else:
                    self.isRedirect='0'
            ## Takes from the first argument the threshold for stub's length
            if str(2*len(self.rev_dict['text']))<=self.options.stubth:
                self.isStub='1'
            else:
                self.isStub='0'
                
            ####CONSTRUCTION OF EXTENDED INSERTS FOR REVISIONS (RESEARCH VERSION)######
            ##Values order: (rev_id, rev_page, rev_user, rev_user_text, rev_timestamp,
            ##rev_len, rev_letters, rev_words, rev_parent_id, rev_is_redirect, rev_is_stub, rev_is_minor, rev_comment)
            # Build current row for revinsert
            newrevinsert="("+self.rev_dict['id']+","+self.page_dict['id']+","+\
            self.rev_dict['rev_user']+","+'"'+self.rev_dict['username'].replace("\\","\\\\").replace("'","\\'").replace('"', '\\"')+\
            '"'+","+'"'+self.rev_dict['timestamp']+\
            '"'+","+str(2*len(self.rev_dict['text']))+\
            ","+str(len(self.rev_dict['text']))+\
            ","+str(len(self.rev_dict['text'].split())) +\
            ","+self.prior_rev_id+","+self.isRedirect+","+self.isStub+","+self.isMinor
            if self.rev_dict.has_key('comment'):
                newrevinsert+=","+'"'+self.rev_dict['comment'].replace("\\","\\\\").replace("'","\\'").replace('"', '\\"')+'"'
            else:
                newrevinsert+=",''"
            newrevinsert+=")"
            if self.revinsertrows==0:
                #Always allow at least one row in extended inserts
                self.revinsert="INSERT INTO revision VALUES"+newrevinsert
                self.revinsertrows+=1
                #Conservative approach: assuming 2 bytes per UTF-8 character
                self.revinsertsize=len(self.revinsert)*2
            elif (self.revinsertsize+(2*len(newrevinsert))<=self.options.imaxsize*1024) and\
            ((self.revinsertrows+1)<=self.options.imaxrows):
                #Append new row to self.revinsert
                self.revinsert+=","+newrevinsert
                self.revinsertrows+=1
                #Conservative approach: assuming 2 bytes per UTF-8 character
                self.revinsertsize=len(self.revinsert)*2
            else:
                #We must finish and write currrent insert and begin a new one
                if self.options.fileout:
                    self.revinsert+=";\n"
                    # Write output to SQL file
                    self.revfile = codecs.open(self.options.revfile,'a','utf_8')
                    self.revfile.write(revinsert)
                    self.revfile.close()
                elif self.options.streamout:
                    # DON'T WRITE SQL TO FILES, GENERATE ENCONDED SQL STREAM FOR MYSQL
                    self.revinsert+=";"
                    print self.revinsert.encode('utf_8')
                elif self.options.monitor:
                    while 1:
                        try:
                            dbaccess.raw_query_SQL(self.acceso[1], self.revinsert.encode('utf_8'))
                        except (Exception), e:
                            print e
                        else:
                            break
                self.revinsert="INSERT INTO revision VALUES"+newrevinsert
                self.revinsertrows=1
                #Conservative approach: assuming 2 bytes per UTF-8 character
                self.revinsertsize=len(self.revinsert)*2
            ##################################################
            ##Store this rev_id to recall it when processing the following revision, if it exists
            self.prior_rev_id=self.rev_dict['id']
            ##Store this rev_len to recall it for the current page_len, in case this is the last revision for that page
            self.last_page_len=2*len(self.rev_dict['text'])
            self.rev_dict.clear()
            self.stack.pop()
            self.isMinor='0'
            self.rev_num+=1
            if self.options.verbose and self.options.log is None:
                # Display status report
                if self.rev_num % 1000 == 0:
                    self.timeCheck=datetime.datetime.now()
                    self.timeDelta=self.timeCheck-self.start
                    if self.timeDelta.seconds==0:
                        print >> sys.stderr, "page %d (%f pags. per sec.), revision %d (%f revs. per sec.)"\
                        % (self.page_num, 1e6*float(self.page_num)/self.timeDelta.microseconds,\
                        self.rev_num, 1e6*float(self.rev_num)/self.timeDelta.microseconds)
                    else:
                        print >> sys.stderr, "page %d (%f pags. per sec.), revision %d (%f revs. per sec.)"\
                        % (self.page_num, float(self.page_num)/self.timeDelta.seconds,\
                        self.rev_num, float(self.rev_num)/self.timeDelta.seconds)
            if self.options.verbose and self.options.log is not None:
                # TODO: Print report status to log file
                pass
        elif name=='page':
            ################################################
            #We must write the las revinsert before finishing this page
            if self.options.fileout:
                self.revinsert+=";\n"
            # Write output to SQL file
                self.revfile = codecs.open(self.options.revfile,'a','utf_8')
                self.revfile.write(self.revinsert)
                self.revfile.close()
            elif self.options.streamout:
                # DON'T WRITE SQL TO FILES, GENERATE ENCONDED SQL STREAM FOR MYSQL
                self.revinsert+=";"
                print self.revinsert.encode('utf_8')
            elif self.options.monitor:
                while 1:
                    try:
                        dbaccess.raw_query_SQL(self.acceso[1], self.revinsert.encode('utf_8'))
                    except (Exception), e:
                        print e
                    else:
                        break
            #Reset status vars
            self.revinsertrows=0
            self.revinsertsize=0
            ################################################
            ##Recovering namespace for this page
            if self.nspace_dict.has_key(self.page_dict['title'].split(':')[0]):
                self.page_dict['namespace']=self.nspace_dict[self.page_dict['title'].split(':')[0]]
            else:
                self.page_dict['namespace']='0'
            #########################################
            #CONSTRUCTION OF EXTENDED INSERT FOR PAGES (RESEARCH VERSION)
            #########################################
            ##Values order for page (page_id, page_namespace, page_title,  page_latest, page_len, page_is_redirect,
            ##page_is_stub, page_random, page_is_new, page_restrictions)
            newpageinsert="("+self.page_dict['id']+","+\
            self.page_dict['namespace']+","+'"'+\
            self.page_dict['title'].replace("\\","\\\\").replace("'","\\'").replace('"', '\\"')+\
            '"'+","+self.prior_rev_id+","+str(self.last_page_len)+\
            ","+self.isRedirect+","+self.isStub+","+str(random.random())
            if self.rev_count>1:
                newpageinsert+=",1"
            else:
                newpageinsert+=",0"
            if self.page_dict.has_key('restrictions'):
                newpageinsert+=","+'"'+self.page_dict['restrictions']+'"'
            else:
                newpageinsert+=",''"
            newpageinsert+=")"
            if self.pageinsertrows==0:
                self.pageinsert="INSERT INTO page VALUES"+newpageinsert
                self.pageinsertrows+=1
                self.pageinsertsize=len(self.pageinsert)*2
            elif (self.pageinsertsize+(2*len(newpageinsert))<=self.options.imaxsize*1024) and\
            (self.pageinsertrows+1<=self.options.imaxrows):
                #Append current row to extended insert
                self.pageinsert+=","+newpageinsert
                self.pageinsertrows+=1
                self.pageinsertsize=len(self.pageinsert)*2
            else:
                #We must write this extended insert and begin a new one
                if self.options.fileout:
                    #Write extended insert to file
                    self.pageinsert+=";\n"
                    self.pagefile = codecs.open(self.options.pagefile,'a','utf_8')
                    self.pagefile.write(self.pageinsert)
                    self.pagefile.close()
                elif self.options.streamout:
                    #Write extended insert to sys.stdout (stream to MySQL)
                    self.pageinsert+=";"
                    print self.pageinsert.encode('utf_8')
                elif self.options.monitor:
                    while 1:
                        try:
                            dbaccess.raw_query_SQL(self.acceso[1], self.pageinsert.encode('utf_8'))
                        except (Exception), e:
                            print e
                        else:
                            break
                self.pageinsert="INSERT INTO page VALUES"+newpageinsert
                self.pageinsertrows=1
                self.pageinsertsize=len(self.pageinsert)*2
            
            ##Clear temp variables for the next page
            self.page_dict.clear()
            self.prior_rev_id='NULL'
            self.last_page_len=0
            self.rev_count=0
            self.isRedirect='0'
            self.isStub='0'
            self.stack.pop()
            self.page_num += 1
                
        else:
            ##General tag processing
            if len(self.stack)>0 and (self.stack[-1]=='revision' or self.stack[-1]=='contributor'):
                self.rev_dict[self.current_elem]=self.current_text
            elif len(self.stack)>0 and self.stack[-1]=='page':
                self.page_dict[self.current_elem]=self.current_text
                
        self.current_elem=None
        return
             
    def characters(self, ch):
        if self.current_elem != None:
            self.current_text = self.current_text + ch
            
    def endDocument(self):
        ################################################
        #We must write the last pageinsert before finishing this dump
        if self.options.fileout:
        # Write output to SQL file
            self.pageinsert+=";\n"
            self.pagefile = codecs.open(self.options.pagefile,'a','utf_8')
            self.pagefile.write(self.pageinsert)
            self.pagefile.close()
        elif self.options.streamout:
            # DON'T WRITE SQL TO FILES, GENERATE ENCONDED SQL STREAM FOR MYSQL
            self.pageinsert+=";"
            print self.pageinsert.encode('utf_8')
        elif self.options.monitor:
            while 1:
                try:
                    dbaccess.raw_query_SQL(self.acceso[1], self.pageinsert.encode('utf_8'))
                except (Exception), e:
                    print e
                else:
                    break
        #Reset status vars
        self.pageinsertrows=0
        self.pageinsertsize=0
        #INSERT NAMESPACES CODES AND TITLES IN SPECIAL TABLE
        nspaces= self.nspace_dict.iteritems()
        insertns='INSERT INTO namespaces VALUES'
        first_loop=True
        for item in nspaces:
            if first_loop:
                first_loop=False
            else:
                insertns+=","
            insertns+='("'+item[0]+'",'+\
            item[1]+')'
        insertns+=";"
        if self.options.fileout:
            nsfile = codecs.open("nspaces.sql",'a','utf_8')
            nsfile.write(insertns)
            nsfile.close()
        if self.options.monitor and (not self.options.fileout and not self.options.streamout):
            while 1:
                    try:
                        dbaccess.raw_query_SQL(self.acceso[1], insertns.encode('utf_8'))
                    except (Exception), e:
                        print e
                    else:
                        break
        ###CLOSE DATABASE CONNECTION IF WE ARE USING MONITOR MODE
        if self.options.monitor and (not self.options.fileout and not self.options.streamout):
            dbaccess.close_Connection(self.acceso[1])
        ################################################
        #Checking out total time consumed and display end message
        self.timeCheck=datetime.datetime.now()
        self.timeDelta=self.timeCheck-self.start
        print >> sys.stderr, "\n"
        print >> sys.stderr, "File successfully parsed..."
        print >> sys.stderr, "page %d (%f pags./sec.), revision %d (%f revs./sec.)" % (self.page_num,\
        float(self.page_num)/self.timeDelta.seconds, self.rev_num, float(self.rev_num)/self.timeDelta.seconds)

##Main zone
if __name__ == '__main__':
    usage = "usage: %prog [options]"
    parserc = OptionParser(usage)
    parserc.add_option("-t","--stubth", dest="stubth", type="int", metavar="STUBTH", default=256,
    help="Max. size in bytes to consider an article as stub [default: %default]")
    parserc.add_option("--pagefile", dest="pagefile", default="page.sql", metavar="FILE",
    help="Name of the SQL file created for the page table [default: %default]")
    parserc.add_option("--revfile", dest="revfile", default="revision.sql", metavar="FILE",
    help="Name of the SQL file created for the revision table [default: %default]")
    parserc.add_option("--textfile", dest="textfile", default="text.sql", metavar="FILE",
    help="Name of the SQL file created for the text table [default: %default]")
    parserc.add_option("--skipnamespaces", dest="skipns", metavar="NAMESPACES",
    help="List of namespaces whose content will be ignored [comma separated values, without "
    "blanks; e.g. --skipnamespaces=name1,name2,name3]")
    parserc.add_option("-i","--inject", dest="inject", metavar="STRING",
    help="Optional string to inject at the very start of articles' text; string "
    "must be provided within quotes (e.g. --inject='my string') or double quotes")
    parserc.add_option("-f","--fileout", dest="fileout", action="store_true", default=False,
    help="Create SQL files from parsed XML dump")
    parserc.add_option("-s","--streamout", dest="streamout", action="store_true", default=False,
    help="Generate an output SQL stream suitable for a direct import into MySQL database")
    parserc.add_option("-m", "--monitor", dest="monitor", action="store_true", default=True,
    help="Insert SQL code directly into MySQL database [default]")
    parserc.add_option("-u", "--user", dest="user", metavar="MySQL_USER",
    help="Username to connect to MySQL database")
    parserc.add_option("-p", "--passwd", dest="passwd", metavar="MySQL_PASSWORD",
    help="Password for MySQL user to access the database")
    parserc.add_option("-d", "--database", dest="database", metavar="DBNAME",
    help="Name of the MySQL database")
    parserc.add_option("--port", dest="port", metavar="MySQL_SERVER_PORT", default=3306, type="int",
    help="Listening port of MySQL server")
    parserc.add_option("--machine", dest="machine", metavar="SERVER_NAME", default="localhost",
    help="Name of MySQL server")
    parserc.add_option("-v", "--verbose", action="store_true", dest="verbose", default=True,
    help="Display standard status reports about the parsing process [default]")
    parserc.add_option("-q", "--quiet", action="store_false", dest="verbose",
    help="Do not display any status reports")
    parserc.add_option("-l","--log", dest="log", metavar="LOGFILE",
    help="Store status reports in a log file; do not display them")
    parserc.add_option("--insertmaxsize", dest="imaxsize", metavar="MAXSIZE", type="int",
    default=156, help="Max size in KB of the MySQL extended inserts [default: %default] "
    "[max: 256]")
    parserc.add_option("--insertmaxnum", dest="imaxrows", metavar="MAXROWS", type="int",
    default=50000, help="Max number of individual rows allowed in the MySQL extended "
    "inserts [default: %default][max: 250000]")
    
    (options, args) = parserc.parse_args()
    if not options.verbose and options.log!=None:
        parserc.error("Error! Illegal combination: options -q and --log options are mutually exclusive")
    if options.monitor and (not options.fileout and not options.streamout) and (options.user==None or options.passwd==None or options.database==None):
        parserc.error("Error! You must provide user, password and database name to execute monitor mode")
    if options.imaxsize>256 or options.imaxsize<=0:
        parserc.error("Error! Illegal value: optional param --insertmaxsize must be between 1 and 256")
    if options.imaxrows>250000 or options.imaxrows<=0:
        parserc.error("Error! Illegal value: optinal param --insertmaxnum must be between 1 250000")
    # Adapt stdout to Unicode UTF-8
    sys.stdout=codecs.EncodedFile(sys.stdout,'utf_8')
    # Create a parser
    parser = make_parser()

    # Tell the parser we are not interested in XML namespaces
    parser.setFeature(feature_namespaces, 0)

    # Create the downstream_handler using our class
    wh = wikiHandler(options)
    
    #Create de filter based in our parser and content handler
    filter_handler = text_normalize_filter(parser, wh)
    #Parse the XML dump
    filter_handler.parse(sys.stdin)