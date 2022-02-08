import copy
from collections import OrderedDict

from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from db.records.operations.group import GroupBy
from mathesar.api.utils import get_table_or_404, process_annotated_records
from mathesar.models import Column


class DefaultLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 500

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ('count', self.count),
                    ('results', data)
                ]
            )
        )


class ColumnLimitOffsetPagination(DefaultLimitOffsetPagination):

    def paginate_queryset(self, queryset, request, table_id):
        self.limit = self.get_limit(request)
        if self.limit is None:
            self.limit = self.default_limit
        self.offset = self.get_offset(request)
        table = get_table_or_404(pk=table_id)
        self.count = len(table.sa_columns)
        self.request = request
        return list(table.sa_columns)[self.offset:self.offset + self.limit]


class TableLimitOffsetPagination(DefaultLimitOffsetPagination):

    def paginate_queryset(
            self, queryset, request, table_id, filters=[], order_by=[], group_by=None,
    ):
        self.limit = self.get_limit(request)
        if self.limit is None:
            self.limit = self.default_limit
        self.offset = self.get_offset(request)
        # TODO: Cache count value somewhere, since calculating it is expensive.
        table = get_table_or_404(pk=table_id)
        self.count = table.sa_num_records(filters=filters)
        self.request = request

        return table.get_records(
            self.limit,
            self.offset,
            filters=filters,
            order_by=order_by,
            group_by=group_by,
        )


class TableLimitOffsetGroupPagination(TableLimitOffsetPagination):
    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ('count', self.count),
                    ('grouping', self.grouping),
                    ('results', data)
                ]
            )
        )

    def iterate_filter_tree_and_apply_field_function(self, obj, field_function):
        if type(obj) == list:
            for filter_object in obj:
                self.iterate_filter_tree_and_apply_field_function(filter_object, field_function)
        elif type(obj) == dict:
            if 'field' in obj.keys():
                field_function(obj)
            else:
                for operator, filter_object in obj.items():
                    self.iterate_filter_tree_and_apply_field_function(filter_object, field_function)

    def extract_field_names(self, field_names):
        def append_to_field_names(filter_obj):
            if filter_obj['op'] == 'get_duplicates':
                field_names.update(filter_obj['value'])
            else:
                field_names.add(filter_obj['field'])

        return append_to_field_names

    def convert_filter_field_id_to_name(self, column_map):
        def x(filter_obj):
            if filter_obj['op'] == 'get_duplicates':
                filter_obj['value'] = [column_map[field_id].name for field_id in filter_obj['value']]
            else:
                field_id = filter_obj['field']
                filter_obj['field'] = column_map[field_id].name

        return x

    def paginate_queryset(
            self, queryset, request, table_id, filters=[], order_by=[], grouping={},
    ):
        columns_ids = set()
        columns_ids.update({column['field'] for column in order_by})
        if grouping:
            columns_ids.update(set(grouping['columns']))
        filter_field_names = set()
        self.iterate_filter_tree_and_apply_field_function(filters, self.extract_field_names(filter_field_names))
        columns_ids.update(filter_field_names)
        columns = Column.objects.filter(id__in=columns_ids)
        columns_name_dict = {column.id: column for column in columns}
        name_converted_order_by = [{**column, 'field': columns_name_dict[column['field']].name} for column in order_by]
        group_by_columns_names = []
        if grouping:
            group_by_columns_names = [columns_name_dict[column_id].name for column_id in grouping['columns']]
        filters_obj = copy.deepcopy(filters)
        self.iterate_filter_tree_and_apply_field_function(
            filters_obj,
            self.convert_filter_field_id_to_name(columns_name_dict)
        )

        name_converted_group_by = {**grouping, 'columns': group_by_columns_names}
        group_by = GroupBy(**name_converted_group_by) if name_converted_group_by else None
        records = super().paginate_queryset(
            queryset,
            request,
            table_id,
            filters=filters_obj,
            order_by=name_converted_order_by,
            group_by=group_by,
        )

        if records:
            processed_records, groups = process_annotated_records(records)
        else:
            processed_records, groups = None, None

        if group_by:
            self.grouping = {
                'columns': group_by.columns,
                'mode': group_by.mode,
                'num_groups': group_by.num_groups,
                'ranged': group_by.ranged,
                'groups': groups,
            }
        else:
            self.grouping = None

        return processed_records
