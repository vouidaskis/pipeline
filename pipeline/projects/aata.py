'''
Classes and utility functions for instantiating, configuring, and
running a bonobo pipeline for converting AATA XML data into JSON-LD.
'''

# AATA Extracters

import sys
import pprint
import itertools

import iso639
import lxml.etree
from sqlalchemy import create_engine
from langdetect import detect
import urllib.parse

import bonobo
from bonobo.config import Configurable, Option, use
from bonobo.constants import NOT_MODIFIED

import settings
from cromulent import model, vocab
from pipeline.projects import PipelineBase
from pipeline.util import identity, ExtractKeyedValues, MatchingFiles, timespan_from_outer_bounds
from pipeline.io.file import MultiFileWriter, MergingFileWriter
# from pipeline.io.arches import ArchesWriter
from pipeline.linkedart import \
			MakeLinkedArtAbstract, \
			MakeLinkedArtLinguisticObject, \
			MakeLinkedArtOrganization, \
			make_la_person, \
			get_crom_object, \
			add_crom_data
from pipeline.io.xml import CurriedXMLReader
from pipeline.nodes.basic import \
			AddArchesModel, \
			CleanDateToSpan, \
			Serializer, \
			Trace
from pipeline.util.cleaners import ymd_to_datetime

legacyIdentifier = None # TODO: aat:LegacyIdentifier?
doiIdentifier = vocab.DoiIdentifier
variantTitleIdentifier = vocab.Identifier # TODO: aat for variant titles?

# utility functions

UID_TAG_PREFIX = 'tag:getty.edu,2019:digital:pipeline:aata:REPLACE-WITH-UUID#'

def aata_uri(*values):
	'''Convert a set of identifying `values` into a URI'''
	if values:
		suffix = ','.join([urllib.parse.quote(str(v)) for v in values])
		return UID_TAG_PREFIX + suffix
	else:
		suffix = str(uuid.uuid4())
		return UID_TAG_PREFIX + suffix

def language_object_from_code(code, language_code_map):
	'''
	Given a three-letter language code (which are mostly drawn from ISO639-2, with some
	exceptions), return a model.Language object for the corresponding language.

	For example, `language_object_from_code('eng')` returns an object representing
	the English language.
	'''
	try:
		if code == 'unk': # TODO: verify that 'unk' is 'unknown' and can be skipped
			return None
		if code in language_code_map:
			language_name = language_code_map[code]
			try:
				return vocab.instances[language_name]
			except KeyError:
				if settings.DEBUG:
					sys.stderr.write(f'*** No AAT language instance found: {language_name!r}\n')
		else:
			if settings.DEBUG:
				sys.stderr.write(f'*** No AAT link for language {code!r}\n')
	except Exception as e:
		sys.stderr.write(f'*** language_object_from_code: {e}\n')
		raise e

# main article chain

def make_aata_journal_dict(e):
	data = _xml_extract_journal(e)
	data.update({
		'object_type': vocab.Journal,
	})
	return data

def make_aata_series_dict(e):
	data = _xml_extract_series(e)
	data.update({
		'object_type': vocab.Series,
	})
# 	print(f'SERIES: {pprint.pformat(data)}')
	return data

def make_publishing_activity(data: dict):
	lo = get_crom_object(data)
	pubs = data.get('publishers', [])
	title = data.get('label')
	for pub in pubs:
		start, end = data['years']
		event = vocab.Publishing()
		event._label = f'Publishing event for “{title}”'
		event.timespan = timespan_from_outer_bounds(start, end)
		lo.used_for = event
# 		event.carried_out_by = org
		if 'events' not in pub:
			pub['events'] = []
		pub['events'].append(event)
	return data

@use('language_code_map')
def make_aata_article_dict(e, language_code_map):
	'''
	Given an XML element representing an AATA record, extract information about the
	"article" (this might be a book, chapter, journal article, etc.) including:

	* document type
	* titles and title translations
	* organizations and their role (e.g. publisher)
	* creators and thier role (e.g. author, editor)
	* abstracts
	* languages

	This information is returned in a single `dict`.
	'''

	data = _xml_extract_article(e, language_code_map)
	aata_id = data['_aata_record_id']
	organizations = list(_xml_extract_organizations(e, aata_id))
	authors = list(_xml_extract_authors(e, aata_id))
	abstracts = list(_xml_extract_abstracts(e, aata_id))

	data.update({
		'_organizations': list(organizations),
		'_authors': list(authors),
		'_abstracts': list(abstracts),
	})
	return data

def _gaia_authority_type(code):
	if code == 'CB':
		return model.Group
	elif code == 'PN':
		return model.Person
	elif code == 'GP':
		return model.Place
	elif code == 'SH':
		return model.Type
	elif code == 'CX':
		# TODO: handle authority
		return model.Type
	elif code == 'TAL':
		# TODO: handle authority
		return model.Type
	else:
		raise LookupError

def _xml_extract_sponsor_group(e):
	aata_id = e.findtext('./sponsor_id')
	name = e.findtext('./sponsor_name')
	geog_id = e.findtext('./auth_geog_id')
	city = e.findtext('./sponsor_city')
	country = e.findtext('./sponsor_country')
	return {
		'label': name,
		'uri': aata_uri('AATA', 'Sponsor', aata_id),
		'_aata_record_id': aata_id,
		'identifiers': [(aata_id, vocab.LocalNumber(ident=''))],
		'place': {
			'label': city,
			'identifiers': [(geog_id, vocab.LocalNumber(ident=''))],
			'type': 'City',
			'part_of': {
				'label': country,
				'type': 'Country',
			}
		}
	}

def _xml_extract_publisher_group(e):
	aata_id = e.findtext('./auth_corp_id')
	name = e.findtext('./publisher_name')
	geog_id = e.findtext('./auth_geog_id')
	city = e.findtext('./publisher_city')
	country = e.findtext('./publisher_country')
	return {
		'label': name,
		'uri': aata_uri('AATA', 'Publisher', aata_id),
		'_aata_record_id': aata_id,
		'identifiers': [(aata_id, vocab.LocalNumber(ident=''))],
		'place': {
			'label': city,
			'identifiers': [(geog_id, vocab.LocalNumber(ident=''))],
			'type': 'City',
			'part_of': {
				'label': country,
				'type': 'Country',
			}
		}
	}

def _xml_extract_journal(e):
	'''Extract information about a journal record XML element'''
	aata_id = e.findtext('./record_desc_group/record_id')
	title = e.findtext('./journal_group/title')
	var_title = e.findtext('./journal_group/variant_title')
	translations = list([t.text for t in
		e.xpath('./journal_group/title_translated')])

	lang_name = e.findtext('./journal_group/language/lang_name')
	lang_scope = e.findtext('./journal_group/language/lang_scope')

	start_year = e.findtext('./journal_group/start_year')
	cease_year = e.findtext('./journal_group/cease_year')
	
	issn = [(t.text, vocab.IssnIdentifier(ident='')) for t in e.xpath('./journal_group/issn')]
	journal_uri = aata_uri('AATA', 'Journal', aata_id)

	publishers = [_xml_extract_publisher_group(p) for p in e.xpath('./publisher_group')]
	sponsors = [_xml_extract_sponsor_group(p) for p in e.xpath('./sponsor_group')]

	issues = []
	volumes = {}
	for ig in e.xpath('./issue_group'):
		issue_id = ig.findtext('./issue_id')
		issue_title = ig.findtext('./title')
		issue_translations = list([t.text for t in
			ig.xpath('./title_translated')])
		volume_number = ig.findtext('./volume')
		issue_number = ig.findtext('./number')
		# TODO: date
		# TODO: volume
		# TODO: number
		# TODO: note
		# TODO: enum_chron
		# TODO: display_form
		
		volumes[volume_number] = {
			'uri': aata_uri('AATA', 'Journal', aata_id, 'Volume', volume_number),
			'object_type': vocab.Volume,
			'label': f'Volume {volume_number} of “{title}”',
			'_aata_record_id': issue_id,
			'identifiers': [(volume_number, vocab.VolumeNumber(ident=''))],
		}
		
		if not issue_title:
			issue_title = f'Issue {issue_number} of “{title}”'

		issues.append({
			'uri': aata_uri('AATA', 'Journal', aata_id, 'Issue', issue_number),
			'object_type': vocab.Issue,
			'label': issue_title,
			'_aata_record_id': issue_id,
			'translations': list(issue_translations),
			'identifiers': [(issue_number, vocab.IssueNumber(ident=''))],
			'volume': volume_number,
		})

	# TODO: journal_history
	# TODO: publisher_group
	# TODO: sponsor_group
	
	var_titles = [(var_title, variantTitleIdentifier(ident=''))] if var_title is not None else []

	data = {
		# TODO: lang_name
		# TODO: lang_scope
		'uri': journal_uri,
		'label': title,
		'_aata_record_id': aata_id,
		'translations': list(translations),
		'identifiers': issn + var_titles,
		'issues': issues,
		'years': [start_year, cease_year],
		'volumes': volumes,
		'publishers': publishers,
		'sponsors': sponsors,
	}
	
	return {k: v for k, v in data.items() if v is not None}

def _xml_extract_series(e):
	'''Extract information about a series record XML element'''
	aata_id = e.findtext('./record_desc_group/record_id')
	title = e.findtext('./series_group/title')
	var_title = e.findtext('./series_group/variant_title')
	translations = list([t.text for t in
		e.xpath('./series_group/title_translated')])

	lang_name = e.findtext('./series_group/language/lang_name')
	lang_scope = e.findtext('./series_group/language/lang_scope')

	start_year = e.findtext('./series_group/start_year')
	cease_year = e.findtext('./series_group/cease_year')
	
	issn = [(t.text, vocab.IssnIdentifier(ident='')) for t in e.xpath('./series_group/issn')]
	publishers = [_xml_extract_publisher_group(p) for p in e.xpath('./publisher_group')]
	sponsors = [_xml_extract_sponsor_group(p) for p in e.xpath('./sponsor_group')]

	# TODO: series_history
	# TODO: sponsor_group
	
	var_titles = [(var_title, variantTitleIdentifier(ident=''))] if var_title is not None else []

	data = {
		# TODO: lang_name
		# TODO: lang_scope
		'uri': aata_uri('AATA', 'Series', aata_id),
		'label': title,
		'_aata_record_id': aata_id,
		'translations': list(translations),
		'identifiers': issn + var_titles,
		'years': [start_year, cease_year],
		'publishers': publishers,
		'sponsors': sponsors,
	}
	
	return {k: v for k, v in data.items() if v is not None}

def _xml_extract_article(e, language_code_map):
	'''Extract information about an "article" record XML element'''
	doc_type = e.findtext('./record_desc_group/doc_type')
	title = e.findtext('./title_group[title_type = "Analytic"]/title')
	var_title = e.findtext('./title_group[title_type = "Analytic"]/title_variant')
	translations = list([t.text for t in
		e.xpath('./title_group[title_type = "Analytic"]/title_translated')])

	doc_langs = {t.text for t in e.xpath('./notes_group/lang_doc')}
	sum_langs = {t.text for t in e.xpath('./notes_group/lang_summary')}

	isbn10e = e.xpath('./notes_group/isbn_10')
	isbn13e = e.xpath('./notes_group/isbn_13')
	issn = [(t.text, vocab.IssnIdentifier(ident='')) for t in e.xpath('./notes_group/issn')]

	isbn = []
	qualified_identifiers = []
	for elements in (isbn10e, isbn13e):
		for t in elements:
			pair = (t.text, vocab.IsbnIdentifier())
			q = t.attrib.get('qualifier')
			if q is None or not q:
				isbn.append(pair)
			else:
				notes = (vocab.Note(content=q),)
# 				print(f'ISBN: {t.text} [{q}]')
				qualified_identifiers.append((t.text, vocab.IsbnIdentifier, notes))

	aata_id = e.findtext('./record_id_group/record_id')
	uid = 'AATA-%s-%s-%s' % (doc_type, aata_id, title)

	classifications = []
	code_type = None # TODO: is there a model.Type value for this sort of code?
	for cg in e.xpath('./classification_group'):
		# TODO: there are only 61 unique classifications in AATA data; map these to UIDs
		cid = cg.findtext('./class_code')
		label = cg.findtext('./class_name')

		name = vocab.PrimaryName(content=label)
		classification = model.Type(label=label)
		classification.identified_by = name

		code = model.Identifier(content=cid)

		code.classified_as = code_type
		classification.identified_by = code
		classifications.append(classification)

	indexings = []
	for ig in e.xpath('./index_group/index/index_id'):
		aid = ig.findtext('./gaia_auth_id')
		atype = ig.findtext('./gaia_auth_type')
		label = ig.findtext('./display_term')
		itype = _gaia_authority_type(atype)
		name = vocab.Title()
		name.content = label

		index = itype(label=label)
		index.identified_by = name

		code = model.Identifier(content=aid)

		code.classified_as = code_type
		index.identified_by = code
		indexings.append(index)

	if title is not None and len(doc_langs) == 1:
		code = doc_langs.pop()
		try:
			language = language_object_from_code(code, language_code_map)
			if language is not None:
				title = (title, language)
		except:
			pass

	var_titles = [(var_title, variantTitleIdentifier(ident=''))] if var_title is not None else []

	return {
		'label': title,
		'document_languages': doc_langs,
		'summary_languages': sum_langs,
		'_document_type': e.findtext('./record_desc_group/doc_type'),
		'_aata_record_id': aata_id,
		'translations': list(translations),
		'identifiers': isbn + issn + var_titles,
		'qualified_identifiers': qualified_identifiers,
		'classifications': classifications,
		'indexing': indexings,
		'uid': uid,
		'uri': aata_uri(uid),
	}

def _xml_extract_abstracts(e, aata_id):
	'''Extract information about abstracts from an "article" record XML element'''
	rids = [e.text for e in e.findall('./record_id_group/record_id')]
	lids = [e.text for e in e.findall('./record_id_group/legacy_id')]
	for i, ag in enumerate(e.xpath('./abstract_group')):
		a = ag.find('./abstract')
		author_abstract_flag = ag.findtext('./author_abstract_flag')
		if a is not None:
			content = a.text
			language = a.attrib.get('lang')

			localIds = [vocab.LocalNumber(content=i) for i in rids]
			legacyIds = [(i, legacyIdentifier) for i in lids]
			yield {
				'_aata_record_id': aata_id,
				'_aata_record_abstract_seq': i,
				'content': content,
				'language': language,
				'author_abstract_flag': (author_abstract_flag == 'yes'),
				'identifiers': localIds + legacyIds,
			}

def _xml_extract_organizations(e, aata_id):
	'''Extract information about organizations from an "article" record XML element'''
	i = -1
	for ig in e.xpath('./imprint_group/related_organization'):
		role = ig.findtext('organization_type')
		properties = {}
		for pair in ig.xpath('./additional_org_info'):
			key = pair.findtext('label')
			value = pair.findtext('value')
			properties[key] = value
		for o in ig.xpath('./organization'):
			i += 1
			aid = o.find('./organization_id')
			if aid is not None:
				name = aid.findtext('display_term')
				auth_id = aid.findtext('gaia_auth_id')
				auth_type = aid.findtext('gaia_auth_type')
				uid = 'AATA-Org-%s-%s-%s' % (auth_type, auth_id, name)
				yield {
					'_aata_record_id': aata_id,
					'_aata_record_organization_seq': i,
					'label': name,
					'role': role,
					'properties': properties,
					'names': [(name,)],
					'object_type': _gaia_authority_type(auth_type),
					'identifiers': [vocab.LocalNumber(ident='', content=auth_id)],
					'uid': uid,
					'uri': aata_uri(uid),
				}
			else:
				print('*** No organization_id found for record %s:' % (o,))
				print(lxml.etree.tostring(o).decode('utf-8'))

def _xml_extract_authors(e, aata_id):
	'''Extract information about authors from an "article" record XML element'''
	i = -1
	for ag in e.xpath('./authorship_group'):
		# TODO: verify that just looping on multiple author_role values produces the expected output
		for role in (t.text for t in ag.xpath('./author_role')):
			for a in ag.xpath('./author'):
				i += 1
				aid = a.find('./author_id')
				if aid is not None:
					name = aid.findtext('display_term')
					auth_id = aid.findtext('gaia_auth_id')
					auth_type = aid.findtext('gaia_auth_type')
# 					if auth_type != 'PN':
# 						print(f'*** Unexpected gaia_auth_type {auth_type} used for author when PN was expected')
					if auth_id is None:
						print('*** no gaia auth id for author in record %r' % (aata_id,))
						uid = 'AATA-P-Internal-%s-%d' % (aata_id, i)
					else:
						uid = 'AATA-P-%s-%s-%s' % (auth_type, auth_id, name)

					author = {
						'_aata_record_id': aata_id,
						'_aata_record_author_seq': i,
						'label': name,
						'names': [(name,)],
						'object_type': _gaia_authority_type(auth_type),
						'identifiers': [vocab.LocalNumber(ident='', content=auth_id)],
						'uid': uid,
						'uri': aata_uri(uid),
					}

					if role is not None:
						author['creation_role'] = role
					else:
						print('*** No author role found for authorship group')
						print(lxml.etree.tostring(ag).decode('utf-8'))

					yield author
				else:
					sys.stderr.write('*** No author_id found for record %s\n' % (aata_id,))
# 					sys.stderr.write(lxml.etree.tostring(a).decode('utf-8'))
# 					sys.stderr.write('\n')

@use('document_types')
def add_aata_object_type(data, document_types):
	'''
	Given an "article" `dict` containing a `_document_type` key which has a two-letter
	document type string (e.g. 'JA' for journal article, 'BC' for book), add a new key
	`object_type` containing the corresponding `vocab` class. This class can be used to
	construct a model object for this "article".

	For example, `add_aata_object_type({'_document_type': 'AV', ...})` returns the `dict`:
	`{'_document_type': 'AV', 'document_type': vocab.AudioVisualContent, ...}`.
	'''
	atype = data['_document_type']
	clsname = document_types[atype]
	data['object_type'] = getattr(vocab, clsname)
	return data

# imprint organizations chain (publishers, distributors)

def add_imprint_orgs(data):
	'''
	Given a `dict` representing an "article," extract the "imprint organization" records
	and their role (e.g. publisher, distributor), and add add a new 'organizations' key
	to the dictionary containing an array of `dict`s representing the organizations.
	Also construct an Activity for each organization's role, and associate it with the
	article and organization (article --role--> organization).

	The resulting organization `dict` will contain these keys:

	* `_aata_record_id`: The identifier of the corresponding article
	* `_aata_record_organization_seq`: A integer identifying this organization
	                                   (unique within the scope of the article)
	* `label`: The name of the organization
	* `role`: The role the organization played in the article's creation (e.g. `'Publishing'`)
	* `properties`: A `dict` of additional properties associated with this organization's
	                role in the article creation (e.g. `DatesOfPublication`)
	* `names`: A `list` of names this organization may be identified by
	* `identifiers`: A `list` of (identifier, identifier type) pairs
	* `uid`: A unique ID for this organization
	* `uuid`: A unique UUID for this organization used in assigning it a URN

	'''
	lod_object = get_crom_object(data)
	organizations = []
	for o in data.get('_organizations', []):
		org = {k: v for k, v in o.items()}
		org_obj = vocab.Group(ident=org['uri'])
		add_crom_data(data=org, what=org_obj)

		event = model.Activity() # TODO: change to vocab.Publishing for publishing activities
		lod_object.used_for = event
		event.carried_out_by = org_obj

		properties = o.get('properties')
		role = o.get('role')
		if role is not None:
			activity_names = {
				'Distributor': 'Distributing',
				'Publisher': 'Publishing',
				# TODO: Need to also handle roles: Organization, Sponsor, University
			}
			if role in activity_names:
				event_label = activity_names[role]
				event._label = event_label
			else:
				print('*** No/unknown organization role (%r) found for imprint_group in %s:' % (
					role, lod_object,))
# 				pprint.pprint(o)

			if role == 'Publisher' and 'DatesOfPublication' in properties:
				pubdate = properties['DatesOfPublication']
				span = CleanDateToSpan.string_to_span(pubdate)
				if span is not None:
					event.timespan = span
		organizations.append(org)
	data['organizations'] = organizations
	return data

def make_aata_org_event(o: dict):
	'''
	Given a `dict` representing an organization, create an `model.Activity` object to
	represent the organization's part in the "article" creation (associating any
	applicable publication timespan to the activity), associate the activity with the
	organization and the corresponding "article", and return a new `dict` that combines
	the input data with an `'events'` key having a `list` value containing the new
	activity object.

	For example,

	```
	make_aata_org_event({
		'event_label': 'Publishing',
		'publication_date_span': model.TimeSpan(...),
		...
	})
	```

	will return:

	```
	{
		'event_label': 'Publishing',
		'publication_date_span': model.TimeSpan(...),
		'events': [model.Activity(_label: 'Publishing', 'timespan': ts.TimeSpan(...))],
		...
	}
	```

	and also set the article object's `used_for` property to the new activity.
	'''
	event = model.Activity()
	lod_object = get_crom_object(o['parent_data'])
	lod_object.used_for = event
	event._label = o.get('event_label')
	if 'publication_date_span' in o:
		ts = o['publication_date_span']
		event.timespan = ts
	org = {k: v for k, v in o.items()}
	org.update({
		'events': [event],
	})
	yield org

# article authors chain

def add_aata_authors(data):
	'''
	Given a `dict` representing an "article," extract the authorship records
	and their role (e.g. author, editor). yield a new `dict`s for each such
	creator (subsequently referred to as simply "author").

	The resulting author `dict` will contain these keys:

	* `_aata_record_id`: The identifier of the corresponding article
	* `_aata_record_author_seq`: A integer identifying this author
	                             (unique within the scope of the article)
	* `label`: The name of the author
	* `creation_role`: The role the author played in the creation of the "article"
	                   (e.g. `'Author'`)
	* `names`: A `list` of names this organization may be identified by
	* `identifiers`: A `list` of (identifier, identifier type) pairs
	* `uid`: A unique ID for this organization
	* `parent`: The model object representing the corresponding article
	* `parent_data`: The `dict` representing the corresponding article
	* `events`: A `list` of `model.Creation` objects representing the part played by
	            the author in the article's creation event.
	'''
	lod_object = get_crom_object(data)
	event = model.Creation()
	lod_object.created_by = event

	authors = data.get('_authors', [])
	for a in authors:
		make_la_person(a)
		person = get_crom_object(a)
		subevent = model.Creation()
		# TODO: The should really be asserted as object -created_by-> CreationEvent -part-> SubEvent
		# however, right now that assertion would get lost as it's data that belongs to the object,
		# and we're on the author's chain in the bonobo graph; object serialization has already happened.
		# we need to serialize the object's relationship to the creation event, and let it get merged
		# with the rest of the object's data.
		event.part = subevent
		role = a.get('creation_role')
		if role is not None:
			subevent._label = 'Creation sub-event for %s' % (role,)
		subevent.carried_out_by = person
	yield data

# article abstract chain

@use('language_code_map')
def detect_title_language(data: dict, language_code_map):
	'''
	Given a `dict` representing a Linguistic Object, attempt to detect the language of
	the value for the `label` key.  If the detected langauge is also one of the languages
	asserted for the record's summaries or underlying document, then update the `label`
	to be a tuple consisting of the original label and a Language model object.
	'''
	dlangs = data.get('document_languages', set())
	slangs = data.get('summary_languages', set())
	languages = dlangs | slangs
	title = data.get('label')
	if isinstance(title, tuple):
		title = title[0]
	try:
		if title is None:
			return NOT_MODIFIED
		translations = data.get('translations', [])
		if translations and languages:
			detected = detect(title)
			threealpha = iso639.to_iso639_2(detected)
			if threealpha in languages:
				language = language_object_from_code(threealpha, language_code_map)
				if language is not None:
					# we have confidence that we've matched the language of the title
					# because it is one of the declared languages for the record
					# document/summary
					data['label'] = (title, language)
			else:
				# the detected language of the title was not declared in the record data,
				# so we lack confidence to proceed
				pass
	except iso639.NonExistentLanguageError as e:
		sys.stderr.write('*** Unrecognized language code detected: %r\n' % (detected,))
	except KeyError as e:
		sys.stderr.write(
			'*** LANGUAGE: detected but unrecognized title language %r '
			'(cf. declared in metadata: %r): %s\n' % (e.args[0], languages, title)
		)
	except Exception as e:
		print('*** detect_title_language error: %r' % (e,))
	return NOT_MODIFIED

@use('language_code_map')
def make_aata_abstract(data, language_code_map):
	'''
	Given a `dict` representing an "article," extract the abstract records.
	yield a new `dict`s for each such record.

	The resulting asbtract `dict` will contain these keys:

	* `_LOD_OBJECT`: A `model.LinguisticObject` object representing the abstract
	* `_aata_record_id`: The identifier of the corresponding article
	* `_aata_record_author_seq`: A integer identifying this abstract
	                             (unique within the scope of the article)
	* `content`: The text content of the abstract
	* `language`: A model object representing the declared langauge of the abstract (if any)
	* `author_abstract_flag`: A boolean value indicating whether the article's authors also
	                          authored the abstract
	* `identifiers`: A `list` of (identifier, identifier type) pairs
	* `_authors`: The authorship information from the input article `dict`
	* `uid`: A unique ID for this abstract
	* `parent`: The model object representing the corresponding article
	* `parent_data`: The `dict` representing the corresponding article
	'''
	lod_object = get_crom_object(data)
	for a in data.get('_abstracts', []):
		abstract_dict = {k: v for k, v in a.items() if k not in ('language',)}

		content = a.get('content')
		abstract = vocab.Abstract(content=content)
		abstract.refers_to = lod_object
		langcode = a.get('language')
		if langcode is not None:
			language = language_object_from_code(langcode, language_code_map)
			if language is not None:
				abstract.language = language
				abstract_dict['language'] = language

		if '_authors' in data:
			abstract_dict['_authors'] = data['_authors']

		# create a uid based on the AATA record id, the sequence number of the abstract
		# in that record, and which author we're handling right now
		uid = 'AATA-Abstract-%s-%d' % (data['_aata_record_id'], a['_aata_record_abstract_seq'])
		abstract_dict.update({
			'parent_data': data,
			'uid': uid,
			'uri': aata_uri(uid),
		})
		add_crom_data(data=abstract_dict, what=abstract)
		yield abstract_dict

def filter_abstract_authors(data: dict):
	'''Yield only those passed `dict` values for which the `'author_abstract_flag'` key is True.'''
	if 'author_abstract_flag' in data and data['author_abstract_flag']:
		yield data

# AATA Pipeline class

class AATAPipeline(PipelineBase):
	'''Bonobo-based pipeline for transforming AATA data from XML into JSON-LD.'''
	def __init__(self, input_path, abstracts_pattern, journals_pattern, series_pattern, **kwargs):
		vocab.register_vocab_class("VolumeNumber", {"parent":model.Identifier, "id":"300265632", "label": "Volume"})
		vocab.register_vocab_class("IssueNumber", {"parent":model.Identifier, "id":"300312349", "label": "Issue"})
		
		self.project_name = 'aata'
		self.graph = None
		self.models = kwargs.get('models', {})
		self.abstracts_pattern = abstracts_pattern
		self.journals_pattern = journals_pattern
		self.series_pattern = series_pattern
		self.limit = kwargs.get('limit')
		self.debug = kwargs.get('debug', False)
		self.input_path = input_path
		self.pipeline_project_service_files_path = kwargs.get('pipeline_project_service_files_path', settings.pipeline_project_service_files_path)
		self.pipeline_common_service_files_path = kwargs.get('pipeline_common_service_files_path', settings.pipeline_common_service_files_path)

		if self.debug:
			self.serializer	= Serializer(compact=False)
			self.writer		= None
			# self.writer	= ArchesWriter()
			sys.stderr.write("In DEBUGGING mode\n")
		else:
			self.serializer	= Serializer(compact=True)
			self.writer		= None
			# self.writer	= ArchesWriter()

	# Set up environment
	def get_services(self):
		'''Return a `dict` of named services available to the bonobo pipeline.'''
		services = super().get_services()
		return services

	def add_serialization_chain(self, graph, input_node):
		'''Add serialization of the passed transformer node to the bonobo graph.'''
		if self.writer is not None:
			graph.add_chain(
				self.serializer,
				self.writer,
				_input=input_node
			)
		else:
			sys.stderr.write('*** No serialization chain defined\n')

	def add_articles_chain(self, graph, records, serialize=True):
		'''Add transformation of article records to the bonobo pipeline.'''
		articles = graph.add_chain(
			make_aata_article_dict,
# 			add_uuid,
			add_aata_object_type,
			detect_title_language,
			MakeLinkedArtLinguisticObject(),
			AddArchesModel(model=self.models['LinguisticObject']),
			add_imprint_orgs,
			_input=records.output
		)
		if serialize:
			# write ARTICLES data
			self.add_serialization_chain(graph, articles.output)
		return articles

	def add_people_chain(self, graph, articles, serialize=True):
		'''Add transformation of author records to the bonobo pipeline.'''
		model_id = self.models.get('Person', 'XXX-Person-Model')
		articles_with_authors = graph.add_chain(
			add_aata_authors,
			_input=articles.output
		)

		if serialize:
			# write ARTICLES with their authorship/creation events data
			self.add_serialization_chain(graph, articles_with_authors.output)

		people = graph.add_chain(
			ExtractKeyedValues(key='_authors'),
			AddArchesModel(model=model_id),
			_input=articles_with_authors.output
		)
		if serialize:
			# write PEOPLE data
			self.add_serialization_chain(graph, people.output)
		return people

	def add_abstracts_chain(self, graph, articles, serialize=True):
		'''Add transformation of abstract records to the bonobo pipeline.'''
		model_id = self.models.get('LinguisticObject', 'XXX-LinguisticObject-Model')
		abstracts = graph.add_chain(
			make_aata_abstract,
			AddArchesModel(model=model_id),
# 			add_uuid,
			MakeLinkedArtAbstract(),
			_input=articles.output
		)

		# for each author of an abstract...
		author_abstracts = graph.add_chain(
			filter_abstract_authors,
			_input=abstracts.output
		)
		self.add_people_chain(graph, author_abstracts)

		if serialize:
			# write ABSTRACTS data
			self.add_serialization_chain(graph, abstracts.output)
		return abstracts

	def add_organizations_chain(self, graph, articles, key='organizations', serialize=True):
		'''Add transformation of organization records to the bonobo pipeline.'''
		model_id = self.models.get('Organization', 'XXX-Organization-Model')
		organizations = graph.add_chain(
			ExtractKeyedValues(key=key),
			AddArchesModel(model=model_id),
			MakeLinkedArtOrganization(),
			_input=articles.output
		)
		if serialize:
			# write ORGANIZATIONS data
			self.add_serialization_chain(graph, organizations.output)
		return organizations

	def _add_abstracts_graph(self, graph):
		abstract_records = graph.add_chain(
			MatchingFiles(path='/', pattern=self.abstracts_pattern, fs='fs.data.aata'),
			CurriedXMLReader(xpath='/AATA_XML/record', fs='fs.data.aata', limit=self.limit)
		)
		articles = self.add_articles_chain(graph, abstract_records)
		self.add_people_chain(graph, articles)
		self.add_abstracts_chain(graph, articles)
		self.add_organizations_chain(graph, articles, key='organizations')
		return articles

	def _add_journals_graph(self, graph, serialize=True):
		journals = graph.add_chain(
			MatchingFiles(path='/', pattern=self.journals_pattern, fs='fs.data.aata'),
			CurriedXMLReader(xpath='/journal_XML/record', fs='fs.data.aata', limit=self.limit),
			make_aata_journal_dict,
			MakeLinkedArtLinguisticObject(),
			make_publishing_activity,
			AddArchesModel(model=self.models['Journal']),
			Trace(name='journal', ordinals=list((2,)))
		)
		
		publishers = self.add_organizations_chain(graph, journals, key='publishers', serialize=serialize)
		sponsors = self.add_organizations_chain(graph, journals, key='sponsors', serialize=serialize)
		
		if serialize:
			# write ARTICLES data
			self.add_serialization_chain(graph, journals.output)
		return journals

	def _add_series_graph(self, graph, serialize=True):
		series = graph.add_chain(
			MatchingFiles(path='/', pattern=self.series_pattern, fs='fs.data.aata'),
			CurriedXMLReader(xpath='/series_XML/record', fs='fs.data.aata', limit=self.limit),
			make_aata_series_dict,
			MakeLinkedArtLinguisticObject(),
			make_publishing_activity,
			AddArchesModel(model=self.models['Series']),
# 			Trace(name='series')
		)
		if serialize:
			# write ARTICLES data
			self.add_serialization_chain(graph, series.output)
		return series

	def _construct_graph(self):
		graph = bonobo.Graph()
		articles = self._add_abstracts_graph(graph)
		journals = self._add_journals_graph(graph)
		
		print('### TODO: skipping series sub-graph')
# 		series = self._add_series_graph(graph)

		self.graph = graph
		return graph

	def get_graph(self):
		'''Construct the bonobo pipeline to fully transform AATA data from XML to JSON-LD.'''
		if not self.graph:
			self._construct_graph()
		return self.graph

	def run(self, **options):
		'''Run the AATA bonobo pipeline.'''
		sys.stderr.write("- Limiting to %d records per file\n" % (self.limit,))
		sys.stderr.write("- Using serializer: %r\n" % (self.serializer,))
		sys.stderr.write("- Using writer: %r\n" % (self.writer,))
		graph = self.get_graph(**options)
		services = self.get_services(**options)
		bonobo.run(
			graph,
			services=services
		)

class AATAFilePipeline(AATAPipeline):
	'''
	AATA pipeline with serialization to files based on Arches model and resource UUID.

	If in `debug` mode, JSON serialization will use pretty-printing. Otherwise,
	serialization will be compact.
	'''
	def __init__(self, input_path, abstracts_pattern, journals_pattern, series_pattern, **kwargs):
		super().__init__(input_path, abstracts_pattern, journals_pattern, series_pattern, **kwargs)
		self.use_single_serializer = False
		self.output_chain = None
		debug = kwargs.get('debug', False)
		output_path = kwargs.get('output_path')
		if debug:
			self.serializer	= Serializer(compact=False)
			self.writer		= MergingFileWriter(directory=output_path, partition_directories=True)
			# self.writer	= MultiFileWriter(directory=output_path)
			# self.writer	= ArchesWriter()
		else:
			self.serializer	= Serializer(compact=True)
			self.writer		= MergingFileWriter(directory=output_path, partition_directories=True)
			# self.writer	= MultiFileWriter(directory=output_path)
			# self.writer	= ArchesWriter()


	def add_serialization_chain(self, graph, input_node):
		'''Add serialization of the passed transformer node to the bonobo graph.'''
		if self.use_single_serializer:
			if self.output_chain is None:
				self.output_chain = graph.add_chain(self.serializer, self.writer, _input=None)

			graph.add_chain(identity, _input=input_node, _output=self.output_chain.input)
		else:
			super().add_serialization_chain(graph, input_node)
