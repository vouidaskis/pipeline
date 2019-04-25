#!/usr/bin/env python3

# TODO: refactor code from knoedler files (e.g. knoedler_linkedart.*) that is actually just linkedart related
# TODO: ensure that multiple serializations to the same uuid are merged. e.g. a journal article with two authors, that each get asserted as carrying out the creation event.

import pprint
import sys, os
from sqlalchemy import create_engine
import bonobo
from bonobo.nodes import Limit
from bonobo.config import Configurable, Option
import itertools
import bonobo_sqlalchemy
import sqlite3

from cromulent import model, vocab
from extracters.xml import XMLReader
from extracters.basic import AddArchesModel, AddFieldNames, Serializer, deep_copy, Offset, add_uuid, Trace
from extracters.aata_data import make_aata_article_dict, make_aata_authors, make_aata_abstract, add_aata_object_type, make_aata_imprint_orgs
from extracters.knoedler_linkedart import *
from extracters.arches import ArchesWriter, FileWriter
from extracters.linkedart import make_la_organization, make_la_record, make_la_abstract
from settings import *

# Set up environment
def get_services(**kwargs):
	return {
		'trace_counter': itertools.count(),
        'gpi': create_engine(gpi_engine),
        'aat': create_engine(aat_engine),
 		'uuid_cache': create_engine(uuid_cache_engine),
		'fs.data.aata': bonobo.open_fs(aata_data_path)
	}

### Pipeline

if DEBUG:
	print("In DEBUGGING mode")
	LIMIT		= os.environ.get('GETTY_PIPELINE_LIMIT', 10)
	PACK_SIZE	= 10
	SRLZ		= Serializer(compact=False)
	WRITER		= FileWriter(directory=output_file_path)
	# WRITER	= ArchesWriter()
else:
	LIMIT		= 10000000
	PACK_SIZE	= 10000000
	SRLZ		= Serializer(compact=True)
	WRITER		= FileWriter(directory=output_file_path)
	# WRITER	= ArchesWriter()


class AddDataDependentArchesModel(Configurable):
	models = Option()
	def __call__(self, data):
		data['_ARCHES_MODEL'] = self.models['LinguisticObject']
		return data

def get_graph(files, **kwargs):
	graph = bonobo.Graph()

	for f in files:
		aata_records = XMLReader(f, xpath='/AATA_XML/record', fs='fs.data.aata')
		articles = graph.add_chain(
			aata_records,
			Limit(LIMIT),
			make_aata_article_dict,
			add_uuid,
			add_aata_object_type,
			make_la_record,
			AddDataDependentArchesModel(models=arches_models),
		)
		
		if False:
			# write ARTICLES data
			graph.add_chain(
				SRLZ,
				WRITER,
				_input=articles.output
			)
		
		people = graph.add_chain(
			make_aata_authors,
			AddArchesModel(model=arches_models['Person']),
			add_uuid,
			make_la_person,
			_input=articles.output
		)
		
		if True:
			# write PEOPLE data
			graph.add_chain(
				SRLZ,
				WRITER,
				_input=people.output
			)

		abstracts = graph.add_chain(
			make_aata_abstract,
			AddArchesModel(model=arches_models['LinguisticObject']),
			add_uuid,
			make_la_abstract,
			_input=people.output
		)

		if True:
			# write ABSTRACTS data
			graph.add_chain(
				SRLZ,
				WRITER,
				_input=abstracts.output
			)

		graph.add_chain(
			make_aata_imprint_orgs,
			AddArchesModel(model='XXX-Organization-Model'), # TODO: model for organizations?
			add_uuid,
			make_la_organization,
			SRLZ,
			WRITER,
			_input=articles.output
		)


	return graph


if __name__ == '__main__':
	files = [f for f in os.listdir(aata_data_path) if f.endswith('.xml')]
	parser = bonobo.get_argument_parser()
	with bonobo.parse_args(parser) as options:
		try:
			bonobo.run(
				get_graph(files=files, **options),
				services=get_services(**options)
			)
		except RuntimeError:
			raise ValueError()

