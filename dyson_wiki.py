#!/usr/bin/python3

"""Utility for maintaining the Dyson Sphere Program wiki.

This command-line script (usually) produces output on stdout in a format that
can be directly cut-and-pasted to the edit box of pages at
https://dyson-sphere-program.fandom.com/. In the usual case where there is
existing content, replace that content entirely, and then use the diff feature
to verify correctness.

To run, this requires the following files in the current directory:
* ItemProtoSet.dat
* RecipeProtoSet.dat
* TechProtoSet.dat
* StringProtoSet.dat

These must be extracted from the game files. See the module help in
"dysonsphere.py" for details.
"""

import argparse
import re
import sys

import dysonsphere
from dysonsphere import ERecipeType, EItemType

# These aren't worth importing from a file
STARTING_RECIPES = [1, 2, 3, 4, 5, 6, 50]
STARTING_TECHS = [1]
MADE_FROM = {
    ERecipeType.NONE:'-',
    ERecipeType.SMELT:'冶炼设备',
    ERecipeType.CHEMICAL:'化工设备',
    ERecipeType.REFINE:'精炼设备',
    ERecipeType.ASSEMBLE:'制造台',
    ERecipeType.PARTICLE:'粒子对撞机',
    ERecipeType.EXCHANGE:'能量交换器',
    ERecipeType.PHOTON_STORE:'射线接收站',
    ERecipeType.FRACTIONATE:'分馏设备',
    ERecipeType.RESEARCH:'科研设备',
    None:'未知'}
BUILDINGS = {
    ERecipeType.SMELT:[2302],
    ERecipeType.CHEMICAL:[2309],
    ERecipeType.REFINE:[2308],
    ERecipeType.ASSEMBLE:[2303, 2304, 2305],
    ERecipeType.PARTICLE:[2310],
    ERecipeType.EXCHANGE:[2209],
    ERecipeType.PHOTON_STORE:[2208],
    ERecipeType.FRACTIONATE:[2314],
    ERecipeType.RESEARCH:[2901]}

CATEGORIES = {
    EItemType.UNKNOWN:'Unknown Category',
    EItemType.RESOURCE:'Natural Resources',
    EItemType.MATERIAL:'Materials',
    EItemType.COMPONENT:'Components',
    EItemType.PRODUCT:'End Products',
    EItemType.LOGISTICS:'Logistics',
    EItemType.PRODUCTION:'Production Facilities',
    EItemType.DECORATION:'Decorations',
    EItemType.WEAPON:'Weapons',
    EItemType.MATRIX:'Science Matrices'}

# Patches we make to be explicit about what techs unlock items.
# This lists the recipe id of recipes to be "fixed": Their first output item
# will be marked as having an explict_tech_dep of the tech that unlocks
# the recipe. This works around cases where multiple recipes, unlocked by
# multiple techs produce the same item.
# to the tech id.
UNLOCK_HACKS = [
    16,  # Plasma Extract Refining
    17,  # Energetic Graphite
    37,  # Crystal Silicon
    78]  # Space Warper

# Tweaks to the sort-key function, to get the recipe list to sort in a better
# order.
KEY_TWEAKS = {75: 103, #Universe Matrix -> After Gravity Matrix
    89: 85, #Conveyor Belt MK.II -> After MK.I
    92: 86, #Conveyor Belt MK.III
    85: 87, #Sorter MK.I
    88: 88, #Sorter MK.II
    90: 89, #Sorter Mk.III
    86: 90, #Storage MK. I
    91: 91, #Storage Mk. II
    87: 92} #Splitter
COLOR_RE = re.compile('<color="([^"]*)">([^<]*)</color>')

def translate_fields(translations, proto_set, fields):
    """In-place replace text with translations for one proto_set."""
    for item in proto_set.data_array:
        for field in fields:
            val = getattr(item, field)
            if val:
                setattr(item, field, translations[val])

def translate_data(data):
    """In-place translate all text fields in 'data'."""
    translations = {}
    for proto in data.StringProtoSet.data_array:
        translations[proto.name] = proto.en_us
    translate_fields(translations, data.ItemProtoSet,
                     ['name', 'mining_from', 'produce_from', 'description'])
    translate_fields(translations, data.RecipeProtoSet, ['name', 'description'])
    translate_fields(translations, data.TechProtoSet, ['name', 'description', 'conclusion'])
    for k, text in MADE_FROM.items():
        MADE_FROM[k] = translations.get(text, text)

def dump_all(data):
    """Print all the game data in raw-ish form.

    (It's still translated.)
    """
    for set_name in ['ItemProtoSet', 'RecipeProtoSet', 'TechProtoSet']:
        print(f'{set_name}:')
        for item in getattr(data, set_name).data_array:
            print(f'    {item}')

def dump_sorted_names(entry_list):
    """Print just the names, sorted.

    Useful for checking if all pages have been created.
    """
    names = [wiki_title(x.name) for x in entry_list]
    names.sort()
    for name in names:
        print(name)

def wiki_title(name):
    """Like title(), except it never lowercases a letter."""
    return ''.join(min(x,y) for x,y in zip(name, name.title()))

def color_sub(desc):
    """Replace all <color="#B9DFFFC4">(rare)</color> tags with equivalent HTML."""
    return COLOR_RE.sub('<span style="color:\\1">\\2</span>', desc)

def format_item(item_entry):
    """Formats an item as a Lua table."""
    item, disabled = item_entry
    if item.id == 1121:
        # Deuterium: We discover this on our own, it creates duplicates
        item.produce_from = None
    fields = {
        'name':repr(wiki_title(item.name)),
        'type':repr(item.type.name),
        'grid_index':item.grid_index,
        'stack_size':item.stack_size,
    }
    if item.can_build:
        fields['can_build'] = 'true'
    if item.is_fluid:
        fields['is_fluid'] = 'true'
    if item.heat_value:
        fields['energy'] = item.heat_value
    if item.reactor_inc:
        fields['fuel_chamber_boost'] = round(item.reactor_inc, 5)
    if item.unlock_key:
        fields['unlock_key'] = item.unlock_key
    if item.pre_tech_override:
        fields['explicit_tech_dep'] = item.pre_tech_override
    if item.mining_from:
        fields['mining_from'] = repr(color_sub(item.mining_from))
    if item.produce_from:
        fields['explicit_produce_from'] = repr(wiki_title(item.produce_from))
    fields['description'] = repr(color_sub(item.description))
    if disabled:
        fields['disabled'] = 'true'
    return (f'    [{item.id}] = {{\n' +
            ''.join(f'        {k}={v},\n' for k, v in fields.items()) +
            f'        --image={item.icon_path.rsplit("/", 1)[1]!r}\n    }},\n')

def format_recipe(recipe_entry):
    """Formats a recipe as a Lua table."""
    rec, disabled = recipe_entry
    if rec.id == 115:
        # Deuterium Fractionation: The game does a bunch of hacks and so do we.
        rec.result_counts[0] /= 100.0
        rec.item_counts[0] /= 100.0
    time_spend = round(rec.time_spend / 60.0, 3)
    if time_spend == int(time_spend):
        time_spend = int(time_spend)  # Changes str() formating
    if 0 < time_spend < 1:
        # We need the extra precision. Format without leading 0.
        time_spend = repr(str(time_spend).lstrip('0'))

    outputs = ', '.join(str(x) for tup in zip(rec.results, rec.result_counts)
            for x in tup)
    inputs = ', '.join(str(x) for tup in zip(rec.items, rec.item_counts)
            for x in tup)
    fields = {
        'id':rec.id,
        'name':repr(wiki_title(rec.name)),
        'type':repr(rec.type.name),
        'outputs':'{' + outputs + '}',
        'inputs':'{' + inputs + '}',
        'grid_index':rec.grid_index,
        'handcraft':'true' if rec.handcraft else 'false',
        'seconds':time_spend,
    }
    if rec.explicit:
        fields['explicit'] = 'true'
    if rec.description:
        fields['description'] = repr(color_sub(rec.description))
    if disabled:
        fields['disabled'] = 'true'
    footer = '    },'
    if rec.icon_path:
        footer = f'        --image={rec.icon_path.rsplit("/", 1)[1]!r}\n    }},'
    return '{\n' + ''.join(f'        {k}={v},\n' for k, v in fields.items()) + footer

def format_tech(tech):
    """Formats a tech as a Lua table."""
    add_items = ', '.join(str(x) for tup in zip(tech.add_items, tech.add_item_counts)
            for x in tup)
    research_counts = [(x * tech.hash_needed) // 3600 for x in tech.item_points]
    research_items = ', '.join(str(x) for tup in zip(tech.items, research_counts)
            for x in tup)
    recipes = ', '.join(str(x) for x in tech.unlock_recipes)
    fields = {
        'id':tech.id,
        'name':repr(wiki_title(tech.name)),
        'hash_needed':tech.hash_needed,
        'inputs':f'{{{research_items}}}',
    }
    if recipes:
        fields['recipes'] = f'{{{recipes}}}'
    if add_items:
        fields['add_items'] = f'{{{add_items}}}'
    fields['description'] = repr(color_sub(tech.description))
    if tech.conclusion:
        fields['conclusion'] = repr(color_sub(tech.conclusion))
    return '{\n' + ''.join(f'        {k}={v},\n' for k, v in fields.items()) + '    },'

def format_facility(facility, items_map):
    """Formats an ERecipeType enum as a Lua table."""
    buildings = ', '.join(str(x) for x in BUILDINGS.get(facility, []))
    building_comment = ', '.join(
            items_map[x][0].name for x in BUILDINGS.get(facility, []))
    return (f'    {facility.name}={{name={MADE_FROM.get(facility, MADE_FROM[None])!r}, ' +
            f'buildings={{{buildings}}}}},  --{building_comment}\n')


def set_valid(items_map, recipe_entry):
    """Set the given recipe_entry valid, and also the associated item(s)"""
    recipe_entry[1] = False
    # Don't bother checking the inputs, we'll assume that a valid tech
    # means they're attainable.
    for iid in recipe_entry[0].results:
        items_map[iid][1] = False

def recipe_key(recipe):
    """Calculate a sort key for recipes"""
    key = recipe.id
    return KEY_TWEAKS.get(key, key)

def create_augmented_maps(data):
    """Create augmented maps to determine whether items/recipes are disabled or not.

    The return is a tuple of (items_map, recipes_map), where each map goes
    from id -> [object, is_disabled]. For recipes, disabled means it should
    never be shown. For items, it should still probably be allowed for direct
    lookups (i.e. Infobox queries), but not for traversals (e.g. creating a
    grid layout of all the items).
    """
    items = data.ItemProtoSet.data_array
    items.sort(key=lambda x:x.id)
    items_map = {}
    # The unlock_key field lets us know for sure that an item is not disabled,
    # and if a recipe is unlocked from a non-disabled tech, or if it is
    # unlocked from the start, then it is not disabled.
    for item in items:
        # Second element is whether item is disabled
        items_map[item.id] = [item, item.unlock_key == 0]

    recipes = data.RecipeProtoSet.data_array
    # The first part of the key determines if this is a building recipe, based
    # on its grid index.
    recipes.sort(key=lambda x:(
        x.grid_index // 1000, x.type.name, recipe_key(x)))
    recipes_map = {}
    for rec in recipes:
        # Second element is whether recipe is disabled
        entry = [rec, True]
        recipes_map[rec.id] = entry
        if rec.id in STARTING_RECIPES:
            set_valid(items_map, entry)

    techs = data.TechProtoSet.data_array
    techs.sort(key=lambda x:x.id)
    for tech in techs:
        if not tech.published:
            continue
        for rid in tech.unlock_recipes:
            set_valid(items_map, recipes_map[rid])
    return items_map, recipes_map

def print_wiki(data):
    """Prints wiki-text dump.

    This is designed to replace (most of) what's at Module:Recipe/Data.
    """
    items_map, recipes_map = create_augmented_maps(data)
    # Deal with UNLOCK_HACKS.
    techs = data.TechProtoSet.data_array
    for tech in techs:
        for hack in UNLOCK_HACKS:
            if hack in tech.unlock_recipes:
                items_map[recipes_map[hack][0].results[0]][0].pre_tech_override = tech.id

    items_str = ''.join(format_item(x) for x in items_map.values())
    recipes_str = ''.join(format_recipe(x) for x in recipes_map.values())
    techs_str = ''.join(format_tech(x) for x in techs)
    facilities_str = ''.join(format_facility(x, items_map) for x in ERecipeType)
    starting_recipes_str = ', '.join(str(x) for x in STARTING_RECIPES)
    categories_str = ''.join(f'    {k.name}={v!r},\n' for k, v in CATEGORIES.items())

    print(f"""return {{
game_items = {{
{items_str}}},
game_recipes = {{
    {recipes_str}
}},
game_techs = {{
    {techs_str}
}},
--[[
These don't come from the game files, because they're totally buried in
the game logic.
This is a map from recipe type (which is effectively facility type) to its name
and an array of item ids of buildings that can produce those types of recipes.
]]
game_facilities = {{
{facilities_str}}},
-- This is just an array of what you start out being able to craft.
-- (Both in the replicator, and in buildings once you get them.)
starting_recipes = {{{starting_recipes_str}}},
-- This maps the symbolic item type names to wiki categories.
-- These don't come from the game files at all, although they're essentially
-- the pluralized version of the equivalent strings.
categories = {{
{categories_str}}},
}}""")

def fuzzy_lookup_item(name_or_id, lst):
    """Lookup an item by either name or id.

    Looking up by id is exact match. Looking up by name is by containment, and
    if the term is entirely lowercase then it's also case-insensitive.
    Multiple matches will throw an exception, unless one of them was an exact
    match.
    """
    try:
        idd = int(name_or_id)
        for val in lst:
            if val.id == idd:
                return val
        raise RuntimeError('Id %d not found!' % idd)
    except ValueError:
        insensitive = name_or_id.islower()
        matches = []
        for val in lst:
            name = val.name
            if name_or_id == name:
                return val
            if insensitive:
                name = name.lower()
            if name_or_id in name:
                matches.append(val)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise RuntimeError(f'No name containing {name_or_id!r} found!') from None
        raise RuntimeError(
            f'Multiple matches for {name_or_id!r}: {[x.name for x in matches]}') from None

# pylint: disable=too-many-branches
def main():
    """Main function, keeps a separate scope"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--find_item',
                        help='Lookup a specific item, by name or id')
    parser.add_argument('--find_recipe',
                        help='Lookup a specific recipe, by name or id')
    parser.add_argument('--find_tech',
                        help='Lookup a specific tech, by name or id')
    parser.add_argument('--dump_item_names', action='store_true',
                        help='Print a sorted list of item names')
    parser.add_argument('--dump_tech_names', action='store_true',
                        help='Print a sorted list of tech names')
    parser.add_argument('--dump_strings', action='store_true',
                        help='Print all the translations, raw')
    parser.add_argument('--dump_all', action='store_true',
                        help='Dump everything')
    parser.add_argument('--wiki', action='store_true',
                        help='Print wiki text for Module:Recipe/Data')
    args = parser.parse_args()

    print('Reading data... ', end='', flush=True, file=sys.stderr)
    data = dysonsphere.load_all()
    translate_data(data)
    print('Done!', flush=True, file=sys.stderr)

    try:
        item = None
        if args.find_item:
            item = fuzzy_lookup_item(
                    args.find_item, data.ItemProtoSet.data_array)
        if args.find_recipe:
            item = fuzzy_lookup_item(
                    args.find_recipe, data.RecipeProtoSet.data_array)
        if args.find_tech:
            item = fuzzy_lookup_item(
                    args.find_tech, data.TechProtoSet.data_array)
        if item:
            print(repr(item))
        else:
            if args.dump_all:
                dump_all(data)
            elif args.dump_item_names:
                dump_sorted_names(data.ItemProtoSet.data_array)
            elif args.dump_tech_names:
                dump_sorted_names(data.TechProtoSet.data_array)
            elif args.dump_strings:
                for entry in data.StringProtoSet.data_array:
                    print(entry)
            elif args.wiki:
                print_wiki(data)
            else:
                print('Nothing to do!', file=sys.stderr)
    except RuntimeError as ex:
        print(ex, file=sys.stderr)

if __name__ == '__main__':
    main()
